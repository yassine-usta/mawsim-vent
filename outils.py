"""Outils communs de l'atelier : lire les serveurs, coder les valeurs en octets, écrire les tuiles.

Tout ce que partagent les familles (familles/*.py, mer.py, feu.py) vit ici ; prepare.py les reprend sous les mêmes
noms, de sorte que le module `prepare` sert toujours de "boîte à outils" aux anciens essais (test_feu.py).

Chaque tuile est un PNG de 181 x 181 points (CELLS mailles + une ligne et une colonne partagées avec les voisines),
le nord en haut, à trois finesses :

  niveau 0 : un point par degré, tuiles de 180° de côté (2 tuiles pour le monde)
  niveau 1 : un demi-degré, tuiles de 90° (8 tuiles)
  niveau 2 : un quart de degré, tuiles de 45° (32 tuiles)

La mer et le feu ajoutent un niveau 3 au douzième de degré (voir mer.py et feu.py).
"""
import datetime as dt
import json
import os
import shutil
import time
import urllib.error
import urllib.request

import numpy as np
from PIL import Image

SOURCE = "https://openmeteo.s3.us-west-2.amazonaws.com/data_spatial"
VERSION = 1
CELLS = 180  # mailles par côté de tuile
LEVELS = (1.0, 0.5, 0.25)  # maille de chaque niveau, en degrés
TIME_FORMAT = "%Y-%m-%dT%H:%MZ"  # la forme des heures dans l'index : "2026-11-21T06:00Z"


def fetch_json(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def parse_time(text):
    """Dates d'Open-Meteo : "2026-11-17T00:00:00Z" ou "2026-11-17T09:00Z"."""
    return dt.datetime.strptime(text[:16], "%Y-%m-%dT%H:%M").replace(tzinfo=dt.timezone.utc)


def format_time(moment):
    """Une heure pour l'index : "2026-11-21T06:00Z" (à la minute, en UTC)."""
    return f"{moment:{TIME_FORMAT}}"


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


def encode_wide(values, low, quantum):
    """Valeur sur deux octets (fort puis faible) : pas de [quantum] à partir de [low]. Pour la température et la pression,
    qu'un seul octet arrondirait trop."""
    steps = np.clip(np.round((values - low) / quantum), 0, 65535).astype(np.uint16)
    return (steps >> 8).astype(np.uint8), (steps & 255).astype(np.uint8)


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


def write_tiles(channels_of, native, folder, levels=LEVELS):
    """Écrit les tuiles de chaque niveau d'une heure pour une famille de grandeurs ([channels_of] rend ses trois
    octets par point pour une maille donnée) ; rend le nombre de tuiles et leur poids."""
    count = size = 0
    for level, step in enumerate(levels):
        factor = int(round(step / native))
        if factor < 1:
            raise SystemExit(f"la grille du modèle ({native}°) est plus large que le niveau {level} ({step}°)")
        # Une colonne de plus, celle du méridien 180, égale à celle du -180 : la Terre est ronde.
        layers = [np.concatenate([a, a[:, :1]], axis=1) for a in channels_of(factor)]
        rows = (layers[0].shape[0] - 1) // CELLS
        cols = (layers[0].shape[1] - 1) // CELLS
        os.makedirs(os.path.join(folder, str(level)), exist_ok=True)
        for row in range(rows):
            for col in range(cols):
                part = (slice(row * CELLS, (row + 1) * CELLS + 1), slice(col * CELLS, (col + 1) * CELLS + 1))
                rgb = np.dstack([layer[part] for layer in layers])
                # Ligne 0 du fichier : le sud. Dans l'image, le nord est en haut.
                path = os.path.join(folder, str(level), f"{row}-{col}.png")
                Image.fromarray(rgb[::-1], "RGB").save(path, optimize=True)
                count += 1
                size += os.path.getsize(path)
    return count, size
