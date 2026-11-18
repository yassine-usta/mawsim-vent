"""Le danger de feu de Mawsim : l'indice forêt-météo (FWI) en tuiles, comme la mer.

Source : le serveur GWIS du service de gestion des urgences de Copernicus (Copernicus Emergency Management
Service), couche "ecmwf.fwi", calculée par ECMWF. Une image du monde par jour, d'aujourd'hui à neuf jours :
5120 x 2560 mailles de 0,0703125°, en nombres réels (environ 52 Mo). Réutilisation permise sous licence
CC BY 4.0 (politique de réutilisation de la Commission européenne), avec la mention "Contains modified
Copernicus Emergency Management Service information [année]".

Chaque jour est téléchargé, passé aux points des tuiles (1/12 de degré), prolongé de deux points vers la mer,
puis écrit en tuiles à quatre finesses par write_tiles de prepare.py :

  - rouge et vert : l'indice sur deux octets (fort puis faible), par pas de 0,01 depuis 0 ;
  - bleu : 0, pas de valeur (la mer, un lac, le désert ou la glace, que le modèle ne calcule pas) ; 255,
    pleine terre du modèle (les quatre mailles du serveur autour du point ont une valeur) ; 128, une valeur
    de bord ou prolongée vers la mer.

Valeur "sans donnée" : dans le TIFF du serveur, c'est 0 exactement. Vérifié le 18 novembre 2026 : l'image
PNG transparente du serveur est transparente sur ces points et seulement sur eux, et le serveur répond
-9999 (pas de valeur) en ces points quand on l'interroge. Un indice très faible sur la terre est un petit
nombre positif (jusqu'à 1e-9), jamais 0.

Le site est refait à chaque calcul d'ICON, mais le feu ne change qu'une fois par jour : avec [cache], les
tuiles du jour sont gardées d'une publication à l'autre et ne sont refaites que si le serveur a publié un
nouveau calcul (un jour de plus au bout) ou si le jour UTC a changé.

Essai : python feu.py SORTIE --days 1 [--tiff IMAGE --today AAAA-MM-JJ]
"""
import argparse
import datetime as dt
import io
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request

import numpy as np
from PIL import Image

import mer

SERVER = "https://maps.effis.emergency.copernicus.eu/gwis"
LAYER = "ecmwf.fwi"
WIDTH, HEIGHT = 5120, 2560  # la grille du serveur, qui est aussi la taille maximale d'une requête
NATIVE = 1.0 / 12.0
LEVELS = (1.0, 0.5, 0.25, NATIVE)
SHORE = 2  # points prolongés vers la mer (environ 18 km)
QUANTUM = 0.01
MAX_DAYS = 10
TRIES = 4
PAUSE = 15  # secondes, multipliées par le numéro de l'essai
USER_AGENT = "Mawsim-atelier/1.0 (fire danger tiles for the Mawsim weather app; one request per forecast day)"
SOURCE = "GWIS, Copernicus Emergency Management Service ; indice forêt-météo d'ECMWF"
META = "fire.json"


def map_url(day, width=WIDTH, height=HEIGHT):
    # En WMS 1.3.0 avec EPSG:4326, la boîte s'écrit latitude puis longitude.
    return (f"{SERVER}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS={LAYER}&STYLES=&CRS=EPSG:4326"
            f"&BBOX=-90,-180,90,180&WIDTH={width}&HEIGHT={height}&FORMAT=image/tiff&TIME={day:%Y-%m-%d}")


def fetch(url, tries=TRIES, pause=PAUSE, timeout=180):
    """Télécharge une image TIFF du serveur, en se nommant ; quelques essais espacés en cas d'échec."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, tries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                kind = response.headers.get("Content-Type", "")
                data = response.read()
            if not kind.startswith("image/tiff"):
                # Le serveur répond aux erreurs par un texte XML, avec le code 200.
                raise ValueError(f"réponse {kind or 'sans type'} : {data[:200]!r}")
            return data
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError) as error:
            if attempt == tries:
                raise
            print(f"  feu : échec ({error}), nouvel essai dans {pause * attempt} s", flush=True)
            time.sleep(pause * attempt)


def read_tiff(data, width=WIDTH, height=HEIGHT):
    """Lit l'image du serveur (une bande de nombres réels, nord en haut) et vérifie sa place sur le globe."""
    with Image.open(io.BytesIO(data)) as image:
        if image.mode != "F" or image.size != (width, height):
            raise ValueError(f"image inattendue : {image.mode} {image.size}")
        tags = image.tag_v2
        scale, tie = tags.get(33550), tags.get(33922)  # GeoTIFF : taille d'une maille, coin nord-ouest
        if scale is not None and abs(scale[0] - 360.0 / width) > 1e-9:
            raise ValueError(f"maille inattendue : {scale}")
        if tie is not None and (abs(tie[3] + 180.0) > 1e-9 or abs(tie[4] - 90.0) > 1e-9):
            raise ValueError(f"coin inattendu : {tie}")
        return np.array(image, dtype=np.float32)


def no_data(raw):
    """Les mailles sans valeur (0 exactement dans le TIFF, ou tout ce qui n'est pas un nombre positif) deviennent NaN."""
    return np.where(np.isfinite(raw) & (raw > 0.0), raw, np.nan).astype(np.float32)


def lattice(field):
    """Passe des mailles du serveur (centrées, nord en haut) aux points ronds des tuiles (2161 lignes du pôle Sud
    au pôle Nord, 4320 colonnes depuis 180° ouest). Chaque point prend la valeur interpolée entre les quatre
    mailles qui l'entourent, en ne comptant que celles qui ont une valeur. Rend aussi la pleine terre : les
    points dont les quatre mailles ont une valeur."""
    height, width = field.shape
    step = 360.0 / width
    rows = int(round(180.0 / NATIVE)) + 1
    cols = int(round(360.0 / NATIVE))
    lat = -90.0 + np.arange(rows) / 12.0
    lon = -180.0 + np.arange(cols) / 12.0
    y = (90.0 - lat) / step - 0.5  # en mailles depuis le nord
    x = (lon + 180.0) / step - 0.5
    j0 = np.floor(y).astype(np.int64)
    i0 = np.floor(x).astype(np.int64)
    fy = (y - j0).astype(np.float32)
    fx = (x - i0).astype(np.float32)
    i1 = (i0 + 1) % width  # la Terre est ronde : la maille à l'ouest de -180° est celle de 179,96°
    i0 = i0 % width
    total = np.zeros((rows, cols), dtype=np.float32)
    weight = np.zeros((rows, cols), dtype=np.float32)
    count = np.zeros((rows, cols), dtype=np.uint8)
    for jj, wy in ((j0, 1.0 - fy), (j0 + 1, fy)):
        inside = (jj >= 0) & (jj < height)  # au-delà des pôles, pas de maille
        band = np.full((rows, width), np.nan, dtype=np.float32)
        band[inside] = field[jj[inside]]
        for ii, wx in ((i0, 1.0 - fx), (i1, fx)):
            values = band[:, ii]
            here = ~np.isnan(values)
            w = wy[:, None] * wx[None, :]
            total += np.where(here, values * w, 0.0)
            weight += np.where(here, w, 0.0)
            count += here
        del band, values, here, w
    with np.errstate(invalid="ignore", divide="ignore"):
        value = np.where(weight > 0.0, total / weight, np.nan).astype(np.float32)
    return value, count == 4


def fire_channels(value, inland, factor, encode_wide):
    """Rouge et vert : l'indice sur deux octets (pas de 0,01 depuis 0) ; bleu : 0 sans valeur, 255 si au moins la moitié
    des points moyennés est en pleine terre, 128 sinon."""
    field = mer.coarser(value, factor)
    share = mer.coarser(np.where(np.isnan(value), np.nan, inland.astype(np.float32)), factor)
    high, low = encode_wide(np.nan_to_num(field, nan=0.0), 0.0, QUANTUM)
    blue = np.where(np.isnan(field), 0, np.where(share >= 0.5, 255, 128)).astype(np.uint8)
    return [high, low, blue]


def write_day(raw, folder, tools):
    """Écrit les tuiles d'un jour depuis l'image du serveur ; rend le nombre de tuiles et leur poids."""
    value, inland = lattice(no_data(raw))
    value = mer.extend(value, SHORE)
    return tools.write_tiles(lambda factor: fire_channels(value, inland, factor, tools.encode_wide), NATIVE, folder, LEVELS)


def entry(days):
    """L'entrée de l'index (contrat format-tuiles-feu.md) pour les jours préparés."""
    return {
        "path": "f/{time}/{level}/{row}-{col}.png",
        "times": [f"{day:%Y-%m-%d}T12:00Z" for day in days],
        "levels": mer.levels(),
        "index": {"channels": "red,green", "low": 0.0, "quantum": QUANTUM, "unit": "FWI"},
        "valid": {"channel": "blue", "from": 128, "inland": 255},
        "model": LAYER,
        "run": f"{days[0]:%Y-%m-%d}T00:00Z",
        "source": SOURCE,
    }


def build(folder, tools, today, days, tiff=None):
    """Prépare les jours d'aujourd'hui au dernier que sert le serveur ([days] au plus), une image à la fois. S'arrête au
    premier jour vide, incomplet ou en erreur. Rend les jours faits."""
    done, tiles, weight, reference = [], 0, 0, None
    for n in range(days):
        day = today + dt.timedelta(days=n)
        started = time.time()
        try:
            if n == 0 and tiff:
                with open(tiff, "rb") as source:
                    data = source.read()
            else:
                data = fetch(map_url(day))
            raw = read_tiff(data)
            del data
        except Exception as error:
            print(f"  feu, {day:%d/%m} : arrêt ({error})", flush=True)
            break
        present = int(np.count_nonzero(raw > 0.0))
        if present == 0:
            print(f"  feu, {day:%d/%m} : image vide, le serveur s'arrête là", flush=True)
            break
        if reference and present < reference // 2:
            # Moins de la moitié des mailles du premier jour : une image coupée, à ne pas publier.
            print(f"  feu, {day:%d/%m} : image incomplète ({present} mailles sur {reference}), arrêt", flush=True)
            break
        reference = reference or present
        count, size = write_day(raw, os.path.join(folder, "f", f"{day:%Y%m%d}12"), tools)
        del raw
        done.append(day)
        tiles += count
        weight += size
        print(f"  feu, {day:%d/%m} : {count} tuiles, {size / 1048576:.1f} Mo ({time.time() - started:.0f} s)", flush=True)
    if done:
        print(f"  feu : {len(done)} jours, {tiles} tuiles, {weight / 1048576:.1f} Mo", flush=True)
    return done


def probe(day):
    """Le serveur a-t-il une image pour ce jour ? Une petite image du monde suffit (500 Ko). Rend None en cas d'échec."""
    try:
        return bool(np.any(read_tiff(fetch(map_url(day, 512, 256), tries=2), 512, 256) > 0.0))
    except Exception as error:
        print(f"  feu : serveur injoignable pour le {day:%d/%m} ({error})", flush=True)
        return None


def read_meta(folder):
    try:
        with open(os.path.join(folder, META), encoding="utf-8") as source:
            meta = json.load(source)
        if os.path.isdir(os.path.join(folder, "f")) and meta.get("entry", {}).get("times"):
            return meta
    except (OSError, ValueError):
        pass
    return None


def still_fresh(meta, today, days, check=probe):
    """Les tuiles gardées valent-elles encore ? Oui si elles sont d'aujourd'hui et que le serveur n'a pas de jour de plus
    au bout (signe d'un nouveau calcul). Si le serveur ne répond pas, on garde ce qu'on a."""
    if meta.get("day") != f"{today:%Y-%m-%d}":
        return False
    times = meta["entry"]["times"]
    if len(times) >= days:
        return True
    last = dt.date.fromisoformat(times[-1][:10])
    return check(last + dt.timedelta(days=1)) is not True


def publish(home, out):
    if os.path.abspath(home) == os.path.abspath(out):
        return
    target = os.path.join(out, "f")
    if os.path.isdir(target):
        shutil.rmtree(target)
    shutil.copytree(os.path.join(home, "f"), target)


def prepare(out, work, tools, cache=None, days=MAX_DAYS, today=None, tiff=None, check=probe):
    """Écrit les tuiles du feu dans [out]/f et rend l'entrée de l'index ({"fire": ...}), ou {} si rien n'a pu être
    préparé : le vent et le reste sont publiés quand même, et l'app garde alors les images du serveur.

    [tools] : les fonctions de prepare.py (write_tiles, encode_wide). [cache] : dossier gardé d'une publication à l'autre
    (f/ et fire.json), réutilisé tant qu'il vaut. [tiff] : image du premier jour lue sur le disque (essais)."""
    try:
        today = today or dt.datetime.now(dt.timezone.utc).date()
        days = max(1, min(days or MAX_DAYS, MAX_DAYS))
        kept = read_meta(cache) if cache else None
        if kept and not tiff and still_fresh(kept, today, days, check):
            print(f"  feu : tuiles du {kept['day']} reprises du cache ({len(kept['entry']['times'])} jours)", flush=True)
            publish(cache, out)
            return {"fire": kept["entry"]}
        fresh = os.path.join(work, "feu-neuf")
        if os.path.isdir(fresh):
            shutil.rmtree(fresh)
        os.makedirs(fresh)
        done = build(fresh, tools, today, days, tiff)
        if not done:
            shutil.rmtree(fresh, ignore_errors=True)
            if kept and kept.get("day") == f"{today:%Y-%m-%d}":
                print("  feu : serveur en échec, tuiles du jour reprises du cache", flush=True)
                publish(cache, out)
                return {"fire": kept["entry"]}
            print("  feu : aucun jour préparé, famille laissée", flush=True)
            return {}
        # [built] change à chaque calcul : la tâche planifiée sait ainsi qu'il faut garder ce nouveau cache.
        meta = {"day": f"{today:%Y-%m-%d}", "built": f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ}", "entry": entry(done)}
        with open(os.path.join(fresh, META), "w", encoding="utf-8") as target:
            json.dump(meta, target, ensure_ascii=False, indent=1)
        home = fresh
        if cache:
            os.makedirs(cache, exist_ok=True)
            for name in ("f", META):
                old = os.path.join(cache, name)
                if os.path.isdir(old):
                    shutil.rmtree(old)
                elif os.path.exists(old):
                    os.remove(old)
                shutil.move(os.path.join(fresh, name), old)
            shutil.rmtree(fresh, ignore_errors=True)
            home = cache
        publish(home, out)
        if home == fresh:
            shutil.rmtree(fresh, ignore_errors=True)
        return {"fire": meta["entry"]}
    except Exception as error:
        print(f"  feu : famille laissée ({error})", flush=True)
        return {}


def main():
    parser = argparse.ArgumentParser(description="Prépare les tuiles du danger de feu de Mawsim (essai).")
    parser.add_argument("out", help="dossier de sortie : f/ et entree-feu.json (l'entrée de l'index)")
    parser.add_argument("--days", type=int, default=MAX_DAYS, help=f"nombre de jours au plus (1 à {MAX_DAYS})")
    parser.add_argument("--cache", help="dossier gardé d'une fois à l'autre (comme dans la tâche planifiée)")
    parser.add_argument("--tiff", help="image du premier jour déjà téléchargée (essais sans réseau)")
    parser.add_argument("--today", help="premier jour, AAAA-MM-JJ (par défaut aujourd'hui en UTC)")
    args = parser.parse_args()

    import prepare as tools  # write_tiles et encode_wide ; prepare importe ce module, d'où l'import tardif

    started = time.time()
    os.makedirs(args.out, exist_ok=True)
    work = tempfile.mkdtemp(prefix="feu-")
    today = dt.date.fromisoformat(args.today) if args.today else None
    try:
        layers = prepare(args.out, work, tools, cache=args.cache, days=args.days, today=today, tiff=args.tiff)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if not layers:
        print("Rien n'a pu être préparé.")
        return 1
    with open(os.path.join(args.out, "entree-feu.json"), "w", encoding="utf-8") as target:
        json.dump(layers["fire"], target, ensure_ascii=False, indent=1)
    print(json.dumps(layers["fire"], ensure_ascii=False))
    print(f"Terminé en {time.time() - started:.0f} s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
