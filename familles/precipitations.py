"""Famille des précipitations par nature : pluie, neige et neige roulée d'ICON, tuiles p/.

Pour la couche Nuages de l'app, qui montre la pluie et la neige chacune dans sa couleur. La pluie de la famille du ciel
(s/, bleu) ne change pas : c'est le total, toutes natures confondues.

Les trois canaux sont des intensités moyennes du pas écoulé, en mm d'eau par heure, codées par la même racine que la pluie
du ciel (octet = 255 x racine(valeur / 60)) :

- rouge : pluie, liquide (pluie de grande échelle "rain" plus pluie des averses "showers") ;
- vert : neige, en eau équivalente ("snowfall_water_equivalent" : neige de grande échelle et des averses) ;
- bleu : le reste du total ("precipitation"), que ni la pluie ni la neige ne comptent. D'après la composition du total
  d'ICON (TOT_PREC), c'est la neige roulée (graupel, GRAU_GSP) : inféré, Open-Meteo ne la publie pas à part. Le
  21 novembre 2026 à 7 h UTC, 80 % de ces points avaient moins de 3 °C à 2 m et le code du temps "averses de neige".

Les quatre grandeurs du fichier sont arrondies au dixième de mm : le reste n'est gardé qu'au-delà de 0,25 mm sur le pas,
pour ne pas prendre les arrondis pour de la neige roulée (jusqu'à 0,2 mm d'écart). Pluie, neige et reste font le total à
0,2 mm près.

Pas écoulé : une heure, puis trois heures au-delà de l'horaire du modèle (comme la pluie du ciel). L'heure du calcul
n'a pas de précipitations dans le fichier : elle est laissée.
"""
import os
import sys

import numpy as np

import outils
import source_icon

NAME = "precipitations"
LAYER = "precipitation"  # sa clé dans index["layers"]
FOLDER = "p"
RATE_MAX = 60.0  # mm par heure, comme la pluie du ciel
ROUNDING = 0.25  # mm sur le pas : en dessous, le reste du total n'est qu'un arrondi


def span_hours(ctx, frame):
    """Durée du pas que couvrent les cumuls de [frame] : une heure, puis trois au-delà de l'horaire du modèle."""
    hours = round((frame.valid - frame.origin).total_seconds() / 3600)
    return 1.0 if hours <= source_icon.HOURLY_UNTIL.get(ctx.model, 10_000) else 3.0


def split(total, rain, showers, snow):
    """Pluie, neige et reste (neige roulée) du pas, en mm, d'après les cumuls du fichier."""
    liquid = rain + showers
    rest = total - liquid - snow
    return liquid, snow, np.where(rest > ROUNDING, rest, 0.0).astype(np.float32)


def precipitation_channels(liquid, solid, rest, factor):
    """Rouge : pluie ; vert : neige ; bleu : neige roulée. En mm par heure, racine jusqu'à 60."""
    return [outils.encode_positive(outils.coarser(value, factor), RATE_MAX) for value in (liquid, solid, rest)]


def write_hour(ctx, frame):
    names = ("precipitation", "rain", "showers", "snowfall_water_equivalent")
    fields = [source_icon.complete_field(frame, name) for name in names]
    if any(field is None for field in fields):
        return None
    span = span_hours(ctx, frame)
    liquid, solid, rest = (part / span for part in split(*fields))
    native = source_icon.native_step(frame.raw("precipitation"))
    folder = os.path.join(ctx.out, FOLDER, f"{frame.valid:%Y%m%d%H}")
    return outils.write_tiles(lambda factor: precipitation_channels(liquid, solid, rest, factor), native, folder)


def entry(ctx, times):
    rate = {"kind": "sqrt", "max": RATE_MAX, "unit": "mm/h"}
    return {
        "path": FOLDER + "/{time}/{level}/{row}-{col}.png",
        "times": [outils.format_time(valid) for valid in times],
        **ctx.provenance(model=ctx.model),
        "rain": {"channel": "red", **rate},
        "snow": {"channel": "green", **rate, "water_equivalent": True},
        "graupel": {"channel": "blue", **rate, "water_equivalent": True},
        "moment": "mean_of_previous_step",
    }


def prepare(ctx):
    return source_icon.prepare_alone(sys.modules[__name__], ctx)
