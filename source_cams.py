"""Le fichier horaire de CAMS, partagé par les familles qui le lisent (poussiere, uv).

CAMS (le service de surveillance de l'atmosphère de Copernicus, calculé par ECMWF) est publié par Open-Meteo sous le nom
`cams_global` : un calcul toutes les 12 heures (0 h et 12 h UTC), 121 échéances horaires (0 à 120 h), une grille mondiale
à 0,4° de 451 lignes (du pôle Sud au pôle Nord) et 900 colonnes (de -180° à 179,6°, un tour entier). Chaque fichier,
d'environ 1 à 2 Mo, porte toutes les grandeurs (poussière, particules, gaz, indice UV...). Il a son propre `latest.json`,
avec son propre calcul : les dates de l'index de ces familles sont celles de CAMS, pas celles d'ICON.

Niveaux de tuiles : la grille source est au 0,4°. Des tuiles au quart de degré n'apporteraient rien, et 0,4° ne tombe pas
sur les degrés ronds des tuiles de l'atelier (le niveau est une maille de 1, 0,5 ou 0,25°, comptée depuis 90° sud et 180°
ouest). Deux niveaux seulement : `0` à 1° et `1` à 0,5°, les mêmes identifiants que le vent. Le niveau 1 est un peu plus
fin que la source (rééchantillonnage bilinéaire : le dessin y est lisse, sans détail en plus) ; le niveau 0 est la moyenne
des points du niveau 1 autour de lui (3 x 3, comme pour ICON).

Une famille de CAMS est un module de familles/ qui expose, en plus de NAME et LAYER :

    FOLDER, VARIABLES   son dossier de tuiles, et les grandeurs de CAMS qu'elle lit (noms chez Open-Meteo)
    THREE_HOURLY (facultatif)  vrai si CAMS ne calcule la grandeur que toutes les trois heures (0, 3, 6... h UTC)
    channels(fields, factor)   les trois octets par point ; [fields] = {nom: grille à 0,5°}
    entry(ctx, cams, times)    son entrée d'index ; [cams] = le plan (calcul, dates) rendu par plan()
"""
import datetime as dt
import os
import time

import numpy as np

import outils

MODEL = "cams_global"
NATIVE = 0.4  # maille de la source, en degrés
BASE = 0.5  # maille de travail : la plus fine des tuiles
LEVELS = (1.0, 0.5)
ROWS, COLS = 451, 900
HOURLY_HOURS = 48  # heure par heure jusque-là (depuis le calcul du vent), puis toutes les trois heures
CONSUMERS = ("poussiere", "uv")  # les familles qui lisent ce fichier : le dernier à finir l'efface


class Plan:
    """Le calcul de CAMS vu à ce passage : son latest.json, son heure de calcul, l'heure où l'atelier l'a vu, et les heures
    à préparer."""

    def __init__(self, latest, run, received, times):
        self.latest = latest
        self.run = run
        self.received = received
        self.times = times

    def provenance(self, ctx):
        return ctx.provenance(model=MODEL, run=self.run, received=self.received, latest=self.latest)


def wanted_times(valid_times, start, parse_time):
    """Heure par heure sur 48 h à partir de [start] (le calcul du vent), puis toutes les trois heures, jusqu'à la fin de
    CAMS (120 h). Les heures d'avant [start] sont passées : l'application n'en veut pas."""
    kept = []
    for text in valid_times:
        valid = parse_time(text)
        hours = (valid - start).total_seconds() / 3600
        if hours < 0:
            continue
        if hours <= HOURLY_HOURS or valid.hour % 3 == 0:
            kept.append(valid)
    return kept


def plan(ctx):
    """Le plan du passage, une seule fois (les familles de CAMS le partagent). Rend None si CAMS n'a pas de calcul complet
    ou ne répond pas : les familles sont alors laissées, le reste est publié."""
    if "cams_plan" in ctx.shared:
        return ctx.shared["cams_plan"]
    tools = ctx.tools
    result = None
    try:
        latest = tools.fetch_json(f"{tools.SOURCE}/{MODEL}/latest.json")
        received = ctx.now or dt.datetime.now(dt.timezone.utc)
        if latest.get("completed", False):
            run = tools.parse_time(latest["reference_time"])
            times = wanted_times(latest["valid_times"], ctx.run or run, tools.parse_time)
            result = Plan(latest, run, received, times) if times else None
        else:
            print("  CAMS : le dernier calcul n'est pas complet, familles laissées", flush=True)
    except Exception as error:
        print(f"  CAMS : latest.json illisible ({error}), familles laissées", flush=True)
    ctx.shared["cams_plan"] = result
    return result


def family_times(ctx, cams, module):
    """Les heures de la famille [module] : celles du plan, et seulement les heures rondes de trois en trois si la famille a
    THREE_HOURLY (la poussière n'est calculée par CAMS que toutes les trois heures), dans la limite de --limit."""
    times = [valid for valid in cams.times if not getattr(module, "THREE_HOURLY", False) or valid.hour % 3 == 0]
    return times[:abs(ctx.limit)] if ctx.limit else times


def levels():
    """Les niveaux, décrits comme ceux du vent (identifiants 0 et 1 : ceux du vent à 1° et 0,5°)."""
    return [{"id": i, "step": step, "span": step * outils.CELLS, "rows": int(round(180 / (step * outils.CELLS))),
             "cols": int(round(360 / (step * outils.CELLS)))} for i, step in enumerate(LEVELS)]


def local_path(ctx, cams, valid):
    return os.path.join(ctx.work, f"{MODEL}-{cams.run:%Y%m%d%H}-{valid:%Y%m%d%H}.om")


def open_reader(path):
    from omfiles import OmFileReader
    return OmFileReader.from_path(path)


def read_fields(path, names):
    """Les grilles [names] du fichier horaire (du sud au nord, float32, 451 x 900) ; ValueError si l'une manque, a une
    autre forme que prévu ou des valeurs manquantes (l'heure est alors laissée)."""
    reader = open_reader(path)
    try:
        fields = {}
        for name in names:
            field = np.asarray(reader.get_child_by_name(name).read_array((slice(None), slice(None))), dtype=np.float32)
            if field.shape != (ROWS, COLS):
                raise ValueError(f"{name} : grille inattendue {field.shape}")
            if np.isnan(field).any():
                raise ValueError(f"{name} : valeurs manquantes")
            fields[name] = field
        return fields
    finally:
        reader.close()


def resample(field):
    """Rééchantillonne la grille de 0,4° sur la maille de 0,5° (bilinéaire ; le tour des longitudes se referme). Rend
    361 x 720 points, de 90° sud et 180° ouest, avec une colonne de moins qu'un tour (write_tiles ajoute celle du 180°)."""
    ratio = BASE / NATIVE
    y = np.arange(int(round(180 / BASE)) + 1) * ratio  # positions en mailles source
    x = np.arange(int(round(360 / BASE))) * ratio
    y0 = np.minimum(np.floor(y).astype(int), ROWS - 2)
    x0 = np.floor(x).astype(int)
    fy = (y - y0)[:, None]
    fx = (x - x0)[None, :]
    x1 = (x0 + 1) % COLS
    top = field[y0][:, x0] * (1 - fx) + field[y0][:, x1] * fx
    bottom = field[y0 + 1][:, x0] * (1 - fx) + field[y0 + 1][:, x1] * fx
    return (top * (1 - fy) + bottom * fy).astype(np.float32)


def release(ctx, name, path, valid):
    """La famille [name] a fini avec ce fichier. Sans --cache, il est effacé quand toutes les familles de CAMS qui le lisent
    l'ont lu (la poussière ne lit que les heures de trois en trois : l'UV est seul à lire les autres)."""
    used = ctx.shared.setdefault("cams_used", {}).setdefault(path, set())
    used.add(name)
    readers = set(CONSUMERS) if valid.hour % 3 == 0 else {"uv"}
    if not ctx.keep_files and used >= readers and os.path.exists(path):
        os.remove(path)


def prepare_family(ctx, module):
    """Prépare toutes les heures du plan pour la famille [module] et rend son entrée d'index, ou None (CAMS indisponible,
    aucune heure préparée). Un fichier déjà là (même passage, autre famille, ou --cache) n'est pas téléchargé deux fois."""
    cams = plan(ctx)
    if cams is None:
        return None
    tools = ctx.tools
    started = time.time()
    done, tiles, weight = [], 0, 0
    for valid in family_times(ctx, cams, module):
        local = local_path(ctx, cams, valid)
        try:
            if not os.path.exists(local):
                tools.download(tools.source_url(MODEL, cams.run, valid), local)
            fields = {name: resample(grid) for name, grid in read_fields(local, module.VARIABLES).items()}
            folder = os.path.join(ctx.out, module.FOLDER, f"{valid:%Y%m%d%H}")
            count, size = tools.write_tiles(lambda factor: module.channels(fields, factor), BASE, folder, LEVELS)
        except Exception as error:  # une heure illisible n'empêche pas les autres
            print(f"  CAMS, {module.NAME} {valid:%d/%m %H h} : laissée ({error})", flush=True)
            continue
        finally:
            release(ctx, module.NAME, local, valid)
        done.append(valid)
        tiles += count
        weight += size
    if not done:
        print(f"  CAMS, {module.NAME} : aucune heure préparée, famille laissée", flush=True)
        return None
    print(f"  CAMS, {module.NAME} : {len(done)} heures, {tiles} tuiles, {weight / 1048576:.1f} Mo ({time.time() - started:.0f} s)",
          flush=True)
    ctx.shared["tiles"] = ctx.shared.get("tiles", 0) + tiles
    ctx.shared["weight"] = ctx.shared.get("weight", 0) + weight
    return module.entry(ctx, cams, done)
