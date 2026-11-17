"""Atelier du vent de Mawsim : prépare le vent mondial en petites tuiles que l'app télécharge.

Source : les grilles publiques d'Open-Meteo (dépôt AWS "openmeteo", dossier data_spatial, licence CC BY 4.0),
modèle ICON du DWD par défaut. Pour chaque heure de prévision, le fichier entier est téléchargé d'un coup
(lire par petits morceaux à distance est des centaines de fois plus lent), le vent à 10 m et les rafales y
sont lus, puis découpés en tuiles PNG de 181 x 181 points à trois finesses :

  niveau 0 : un point par degré, tuiles de 180° de côté (2 tuiles pour le monde)
  niveau 1 : un demi-degré, tuiles de 90° (8 tuiles)
  niveau 2 : un quart de degré, tuiles de 45° (32 tuiles)

Dans chaque tuile, le nord est en haut ; le rouge porte le vent vers l'est, le vert le vent vers le nord,
le bleu les rafales. Les valeurs sont codées par une racine (fines près de zéro, voir encode_signed).
Chaque tuile partage sa dernière ligne et sa dernière colonne avec ses voisines : le téléphone interpole
sans couture. Le fichier index.json décrit le tout ; il est écrit en dernier.

Usage : python prepare.py SORTIE [--model dwd_icon] [--cache DOSSIER] [--limit N] [--published URL]
"""
import argparse
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request

import numpy as np
from omfiles import OmFileReader
from PIL import Image

SOURCE = "https://openmeteo.s3.us-west-2.amazonaws.com/data_spatial"
VERSION = 1
U_MAX = 50.0  # m/s : au-delà, la valeur est plafonnée (180 km/h)
GUST_MAX = 70.0  # m/s (252 km/h)
CELLS = 180  # mailles par côté de tuile
LEVELS = (1.0, 0.5, 0.25)  # maille de chaque niveau, en degrés
HOURLY_HOURS = 48  # heure par heure jusque-là, puis toutes les trois heures
LAST_HOUR = 180  # sept jours et demi


def fetch_json(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def parse_time(text):
    """Dates d'Open-Meteo : "2026-11-17T00:00:00Z" ou "2026-11-17T09:00Z"."""
    return dt.datetime.strptime(text[:16], "%Y-%m-%dT%H:%M").replace(tzinfo=dt.timezone.utc)


def source_url(model, run, valid):
    return f"{SOURCE}/{model}/{run:%Y/%m/%d/%H%M}Z/{valid:%Y-%m-%dT%H%M}.om"


def download(url, path, tries=4):
    """Télécharge le fichier entier, en reprenant après une coupure."""
    for attempt in range(1, tries + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as response, open(path, "wb") as out:
                shutil.copyfileobj(response, out, 1 << 20)
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
            if attempt == tries:
                raise
            print(f"  coupure ({error}), nouvel essai dans {5 * attempt} s", flush=True)
            time.sleep(5 * attempt)


def encode_signed(values, top):
    """Racine signée sur un octet : pas de 0,1 m/s vers 2 m/s, de 0,4 m/s dans la tempête."""
    x = np.clip(values / top, -1.0, 1.0)
    return np.round(127.5 + 127.5 * np.sign(x) * np.sqrt(np.abs(x))).astype(np.uint8)


def encode_positive(values, top):
    return np.round(255.0 * np.sqrt(np.clip(values / top, 0.0, 1.0))).astype(np.uint8)


def read_field(reader, name):
    variable = reader.get_child_by_name(name)
    return np.asarray(variable.read_array((slice(None), slice(None))), dtype=np.float32)


def full_circle(field, step):
    """Complète la grille pour qu'elle compte un tour entier de longitudes (ICON s'arrête une colonne avant)."""
    wanted = int(round(360.0 / step))
    missing = wanted - field.shape[1]
    if missing <= 0:
        return field[:, :wanted]
    # Les colonnes manquantes, juste avant le méridien 180, sont interpolées entre leurs deux voisines.
    last, first = field[:, -1:], field[:, :1]
    extra = [last + (first - last) * (i + 1) / (missing + 1) for i in range(missing)]
    return np.concatenate([field] + extra, axis=1)


def coarser(field, factor, peak=False):
    """Maille factor fois plus large : moyenne autour de chaque point gardé (maximum pour les rafales)."""
    if factor == 1:
        return field
    pad = factor // 2
    wide = np.concatenate([field[:, -pad:], field, field[:, :pad]], axis=1)
    wide = np.pad(wide, ((pad, pad), (0, 0)), mode="edge")
    rows = (field.shape[0] - 1) // factor + 1
    cols = field.shape[1] // factor
    windows = [
        wide[dy:dy + (rows - 1) * factor + 1:factor, dx:dx + (cols - 1) * factor + 1:factor]
        for dy in range(2 * pad + 1) for dx in range(2 * pad + 1)
    ]
    return np.max(windows, axis=0) if peak else np.mean(windows, axis=0)


def write_tiles(u, v, gust, native, folder):
    """Écrit les tuiles des trois niveaux d'une heure ; rend le nombre de tuiles et leur poids."""
    count = size = 0
    for level, step in enumerate(LEVELS):
        factor = int(round(step / native))
        if factor < 1:
            raise SystemExit(f"la grille du modèle ({native}°) est plus large que le niveau {level} ({step}°)")
        # Une colonne de plus, celle du méridien 180, égale à celle du -180 : la Terre est ronde.
        layers = [np.concatenate([a, a[:, :1]], axis=1) for a in (coarser(u, factor), coarser(v, factor), coarser(gust, factor, peak=True))]
        rows = (layers[0].shape[0] - 1) // CELLS
        cols = (layers[0].shape[1] - 1) // CELLS
        os.makedirs(os.path.join(folder, str(level)), exist_ok=True)
        for row in range(rows):
            for col in range(cols):
                part = (slice(row * CELLS, (row + 1) * CELLS + 1), slice(col * CELLS, (col + 1) * CELLS + 1))
                rgb = np.dstack([encode_signed(layers[0][part], U_MAX), encode_signed(layers[1][part], U_MAX), encode_positive(layers[2][part], GUST_MAX)])
                # Ligne 0 du fichier : le sud. Dans l'image, le nord est en haut.
                path = os.path.join(folder, str(level), f"{row}-{col}.png")
                Image.fromarray(rgb[::-1], "RGB").save(path, optimize=True)
                count += 1
                size += os.path.getsize(path)
    return count, size


def wanted_times(run, valid_times):
    """Heure par heure sur 48 h, puis toutes les trois heures jusqu'à sept jours et demi."""
    kept = []
    for text in valid_times:
        valid = parse_time(text)
        hours = round((valid - run).total_seconds() / 3600)
        if hours < 0 or hours > LAST_HOUR:
            continue
        if hours <= HOURLY_HOURS or hours % 3 == 0:
            kept.append(valid)
    return kept


def main():
    parser = argparse.ArgumentParser(description="Prépare les tuiles de vent de Mawsim.")
    parser.add_argument("out", help="dossier de sortie (le site publié)")
    parser.add_argument("--model", default="dwd_icon")
    parser.add_argument("--cache", help="dossier où garder les fichiers téléchargés (essais)")
    parser.add_argument("--limit", type=int, default=0, help="nombre d'heures à préparer (essais) ; 0 : toutes")
    parser.add_argument("--published", help="adresse de l'index déjà publié : rien n'est refait si le calcul est le même")
    args = parser.parse_args()

    started = time.time()
    latest = fetch_json(f"{SOURCE}/{args.model}/latest.json")
    if not latest.get("completed", False):
        print("Le dernier calcul n'est pas encore complet chez Open-Meteo : rien à faire.")
        return 2
    run = parse_time(latest["reference_time"])
    if args.published:
        try:
            current = fetch_json(args.published, timeout=30)
            if current.get("version") == VERSION and current.get("model") == args.model and current.get("run") == f"{run:%Y-%m-%dT%H:%MZ}":
                print(f"Le calcul du {run:%d/%m à %H h} UTC est déjà publié : rien à faire.")
                return 2
        except Exception as error:  # premier passage, ou site injoignable : on prépare
            print(f"Index publié illisible ({error}) : on prépare.")

    times = wanted_times(run, latest["valid_times"])
    if args.limit:
        times = times[:args.limit]
    print(f"Modèle {args.model}, calcul du {run:%d/%m/%Y à %H h} UTC, {len(times)} heures à préparer.", flush=True)

    if os.path.isdir(args.out):
        shutil.rmtree(args.out)
    os.makedirs(args.out)
    work = args.cache or tempfile.mkdtemp(prefix="vent-")
    os.makedirs(work, exist_ok=True)
    done, tiles, weight, native = [], 0, 0, None
    for valid in times:
        name = f"{valid:%Y%m%d%H}"
        local = os.path.join(work, f"{args.model}-{run:%Y%m%d%H}-{name}.om")
        if not (args.cache and os.path.exists(local)):
            download(source_url(args.model, run, valid), local)
        try:
            reader = OmFileReader.from_path(local)
            u = read_field(reader, "wind_u_component_10m")
            v = read_field(reader, "wind_v_component_10m")
            try:
                gust = read_field(reader, "wind_gusts_10m")
            except ValueError:
                # L'heure du calcul lui-même n'a pas de rafales (elles se mesurent sur l'heure écoulée) :
                # le vent moyen en tient lieu, jamais moins.
                gust = np.hypot(u, v)
            reader.close()
        finally:
            if not args.cache:
                os.remove(local)
        native = 180.0 / (u.shape[0] - 1)
        u, v, gust = (full_circle(a, native) for a in (u, v, gust))
        if np.isnan(u).any() or np.isnan(v).any():
            # Une heure incomplète ne doit pas partir dans l'app : on l'écarte, les autres restent.
            print(f"  {valid:%d/%m %H h} : valeurs manquantes, heure écartée", flush=True)
            continue
        gust = np.where(np.isnan(gust), np.hypot(u, v), gust)
        count, size = write_tiles(u, v, gust, native, os.path.join(args.out, "t", name))
        done.append(valid)
        tiles += count
        weight += size
        print(f"  {valid:%d/%m %H h} : {count} tuiles, {size / 1024:.0f} Ko ({time.time() - started:.0f} s)", flush=True)

    if len(done) < max(1, len(times) // 2):
        raise SystemExit("Moins de la moitié des heures a pu être préparée : rien n'est publié.")
    index = {
        "version": VERSION,
        "model": args.model,
        "run": f"{run:%Y-%m-%dT%H:%MZ}",
        "created": f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%MZ}",
        "source": "Open-Meteo (CC BY 4.0), modèle ICON du DWD" if args.model == "dwd_icon" else f"Open-Meteo (CC BY 4.0), {args.model}",
        "encoding": {"kind": "sqrt", "wind_max": U_MAX, "gust_max": GUST_MAX, "red": "u", "green": "v", "blue": "gust"},
        "tile": {"cells": CELLS, "north_up": True},
        "levels": [{"id": i, "step": step, "span": step * CELLS, "rows": int(round(180 / (step * CELLS))), "cols": int(round(360 / (step * CELLS)))} for i, step in enumerate(LEVELS)],
        "path": "t/{time}/{level}/{row}-{col}.png",
        "times": [f"{valid:%Y-%m-%dT%H:%MZ}" for valid in done],
    }
    with open(os.path.join(args.out, "index.json"), "w", encoding="utf-8") as out:
        json.dump(index, out, ensure_ascii=False, indent=1)
    print(f"Terminé : {len(done)} heures, {tiles} tuiles, {weight / 1048576:.0f} Mo, en {time.time() - started:.0f} s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
