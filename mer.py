"""La mer de Mawsim : température de l'eau et courants de surface, en tuiles comme le vent.

Source : les grilles publiques d'Open-Meteo (data_spatial, licence CC BY 4.0), modèles de Météo-France
"meteofrance_sea_surface_temperature" (toutes les 6 heures) et "meteofrance_currents" (toutes les heures),
au douzième de degré, mondiaux. Les fichiers sont légers (2 et 6 Mo par heure).

Deux différences avec le vent :

  - la mer a un niveau de plus, au douzième de degré (la maille du modèle) : tuiles de 15° de côté ;
  - la terre n'a pas de valeur. Le bleu de chaque tuile dit si le point en a une (255) ou non (0). Les
    valeurs sont prolongées de quelques mailles vers la terre : le téléphone colorie ainsi la mer jusqu'à
    la vraie côte, qu'il connaît mieux que le modèle, et efface lui-même ce qui dépasse sur la terre.

Les points du modèle sont au centre de ses mailles (-179,958°, -79,958°...) ; ceux des tuiles sont sur les
degrés ronds. Chaque point de tuile est donc la moyenne des quatre points du modèle qui l'entourent.
"""
import datetime as dt
import os
import warnings

import numpy as np
from omfiles import OmFileReader

NATIVE = 1.0 / 12.0
LEVELS = (1.0, 0.5, 0.25, NATIVE)
SHAPE = (2041, 4320)  # la grille attendue : de 79,96° sud à 90° nord, un tour de longitudes
FIRST_ROW = 121  # premier point de tuile (79,917° sud) entouré de points du modèle
TEMPERATURE_MODEL = "meteofrance_sea_surface_temperature"
CURRENT_MODEL = "meteofrance_currents"
TEMPERATURE_LOW = -5.0  # °C
TEMPERATURE_QUANTUM = 0.01
CURRENT_MAX = 3.0  # m/s
SHORE = 3  # mailles prolongées vers la terre (25 km)
# Un nouveau calcul arrive toutes les 6 heures : la température couvre 18 heures ; les courants, la frise de 24 heures
# et 12 heures de marge. Au-delà, l'app revient d'elle-même aux cartes de Copernicus.
TEMPERATURE_AHEAD = 18
CURRENT_AHEAD = 36


def nan_mean(arrays):
    """Moyenne point par point de plusieurs grilles, sans compter les valeurs absentes (absente si toutes le sont)."""
    total = np.zeros(arrays[0].shape, dtype=np.float32)
    count = np.zeros(arrays[0].shape, dtype=np.float32)
    for a in arrays:
        here = ~np.isnan(a)
        total += np.where(here, a, 0.0)
        count += here
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.where(count > 0, total / count, np.nan).astype(np.float32)


def lattice(data):
    """Passe des centres de mailles du modèle aux points ronds des tuiles : 2161 lignes du pôle Sud au pôle Nord."""
    west = np.roll(data, 1, axis=1)
    rows = int(round(180.0 / NATIVE)) + 1
    out = np.full((rows, data.shape[1]), np.nan, dtype=np.float32)
    out[FIRST_ROW:FIRST_ROW + data.shape[0] - 1] = nan_mean([data[:-1], data[1:], west[:-1], west[1:]])
    return out


def extend(field, cells=SHORE):
    """Prolonge les valeurs de [cells] mailles vers la terre : chaque point vide prend la moyenne de ses voisins."""
    out = field
    for _ in range(cells):
        padded = np.pad(out, ((1, 1), (0, 0)), constant_values=np.nan)
        around = [np.roll(padded, dx, axis=1)[1 + dy:1 + dy + out.shape[0]] for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dy, dx) != (0, 0)]
        out = np.where(np.isnan(out), nan_mean(around), out)
    return out


def coarser(field, factor):
    """Maille factor fois plus large : moyenne des valeurs présentes autour de chaque point gardé."""
    if factor == 1:
        return field
    pad = factor // 2
    wide = np.concatenate([field[:, -pad:], field, field[:, :pad]], axis=1)
    wide = np.pad(wide, ((pad, pad), (0, 0)), constant_values=np.nan)
    rows = (field.shape[0] - 1) // factor + 1
    cols = field.shape[1] // factor
    return nan_mean([
        wide[dy:dy + (rows - 1) * factor + 1:factor, dx:dx + (cols - 1) * factor + 1:factor]
        for dy in range(2 * pad + 1) for dx in range(2 * pad + 1)
    ])


def valid_channel(field):
    """Bleu : 255 là où le point a une valeur, 0 ailleurs."""
    return np.where(np.isnan(field), 0, 255).astype(np.uint8)


def temperature_channels(temperature, factor, encode_wide):
    """Rouge et vert : température de l'eau sur deux octets (centièmes de degré depuis -5 °C) ; bleu : présence."""
    field = coarser(temperature, factor)
    high, low = encode_wide(np.nan_to_num(field, nan=TEMPERATURE_LOW), TEMPERATURE_LOW, TEMPERATURE_QUANTUM)
    return [high, low, valid_channel(field)]


def current_channels(u, v, factor, encode_signed):
    """Rouge : courant vers l'est ; vert : courant vers le nord (racine signée jusqu'à 3 m/s) ; bleu : présence."""
    east, north = coarser(u, factor), coarser(v, factor)
    return [encode_signed(np.nan_to_num(east), CURRENT_MAX), encode_signed(np.nan_to_num(north), CURRENT_MAX), valid_channel(east)]


def wanted(valid_times, now, every, ahead, parse_time):
    """Les heures utiles : de la dernière heure ronde (tous les [every] heures) à [ahead] heures plus tard."""
    start = now.replace(minute=0, second=0, microsecond=0)
    start -= dt.timedelta(hours=start.hour % every)
    end = now + dt.timedelta(hours=ahead)
    times = [parse_time(text) for text in valid_times]
    return [t for t in times if start <= t <= end and t.hour % every == 0]


def read(path, name):
    reader = OmFileReader.from_path(path)
    try:
        field = np.asarray(reader.get_child_by_name(name).read_array((slice(None), slice(None))), dtype=np.float32)
    finally:
        reader.close()
    if field.shape != SHAPE:
        raise ValueError(f"grille inattendue {field.shape}")
    return field


def levels():
    return [{"id": i, "step": step, "span": step * 180, "rows": int(round(1 / step)), "cols": int(round(2 / step))} for i, step in enumerate(LEVELS)]


def prepare(out, work, tools, keep_files=False, limit=0, now=None):
    """Écrit les tuiles de la mer dans [out] et rend leurs entrées pour l'index. Une famille qui échoue est
    laissée de côté : le vent et le reste sont publiés quand même, et l'app garde alors les cartes de Copernicus.

    [tools] : les fonctions de prepare.py (fetch_json, parse_time, source_url, download, write_tiles, encode_wide,
    encode_signed)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    layers = {}
    plans = (
        ("sea", "m", TEMPERATURE_MODEL, 6, TEMPERATURE_AHEAD),
        ("current", "c", CURRENT_MODEL, 3, CURRENT_AHEAD),
    )
    for family, folder, model, every, ahead in plans:
        try:
            latest = tools.fetch_json(f"{tools.SOURCE}/{model}/latest.json")
            run = tools.parse_time(latest["reference_time"])
            times = wanted(latest["valid_times"], now, every, ahead, tools.parse_time)
            if limit:
                times = times[:limit]
            done, tiles, weight = [], 0, 0
            for valid in times:
                name = f"{valid:%Y%m%d%H}"
                local = os.path.join(work, f"{model}-{run:%Y%m%d%H}-{name}.om")
                try:
                    if not (keep_files and os.path.exists(local)):
                        tools.download(tools.source_url(model, run, valid), local)
                    if family == "sea":
                        temperature = extend(lattice(read(local, "sea_surface_temperature")))
                        channels = lambda factor: temperature_channels(temperature, factor, tools.encode_wide)
                    else:
                        u = extend(lattice(read(local, "ocean_u_current")))
                        v = extend(lattice(read(local, "ocean_v_current")))
                        channels = lambda factor: current_channels(u, v, factor, tools.encode_signed)
                    count, size = tools.write_tiles(channels, NATIVE, os.path.join(out, folder, name), LEVELS)
                except Exception as error:  # une heure illisible n'empêche pas les autres
                    print(f"  mer, {family} {valid:%d/%m %H h} : laissée ({error})", flush=True)
                    continue
                finally:
                    if not keep_files and os.path.exists(local):
                        os.remove(local)
                done.append(valid)
                tiles += count
                weight += size
            if not done:
                print(f"  mer, {family} : aucune heure préparée, famille laissée", flush=True)
                continue
            entry = {
                "path": folder + "/{time}/{level}/{row}-{col}.png",
                "times": [f"{valid:%Y-%m-%dT%H:%MZ}" for valid in done],
                "levels": levels(),
                "model": model,
                "run": f"{run:%Y-%m-%dT%H:%MZ}",
                "source": "Open-Meteo (CC BY 4.0), modèle océanique de Météo-France",
                "valid": {"channel": "blue", "from": 128},
            }
            if family == "sea":
                entry["temperature"] = {"channels": "red,green", "low": TEMPERATURE_LOW, "quantum": TEMPERATURE_QUANTUM, "unit": "°C"}
            else:
                entry["current"] = {"east": "red", "north": "green", "kind": "sqrt", "max": CURRENT_MAX, "unit": "m/s"}
            layers[family] = entry
            print(f"  mer, {family} : {len(done)} heures, {tiles} tuiles, {weight / 1048576:.1f} Mo", flush=True)
        except Exception as error:
            print(f"  mer, {family} : famille laissée ({error})", flush=True)
    return layers
