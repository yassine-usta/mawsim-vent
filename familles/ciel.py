"""Famille du ciel : la pression au niveau de la mer et la pluie d'ICON, tuiles s/.

Rouge et vert : pression sur deux octets (dixièmes d'hectopascal depuis 850 hPa) ; bleu : pluie de l'heure (mm par
heure, codée par une racine jusqu'à 60). Une heure sans pression est laissée ; sans pluie, la pluie vaut zéro.
"""
import os
import sys

import numpy as np

import outils
import source_icon

NAME = "ciel"
LAYER = "sky"  # sa clé dans index["layers"]
FOLDER = "s"
PRESSURE_LOW = 850.0  # hPa
RAIN_MAX = 60.0  # mm par heure


def sky_channels(pressure, rain, factor):
    """Rouge et vert : pression au niveau de la mer sur deux octets (dixièmes d'hectopascal depuis 850 hPa) ; bleu : pluie
    de l'heure (mm par heure, codée par une racine jusqu'à 60)."""
    high, low = outils.encode_wide(outils.coarser(pressure, factor), PRESSURE_LOW, 0.1)
    return [high, low, outils.encode_positive(outils.coarser(rain, factor), RAIN_MAX)]


def write_hour(ctx, frame):
    pressure = source_icon.complete_field(frame, "pressure_msl")
    if pressure is None:
        return None
    rain = source_icon.complete_field(frame, "precipitation")
    # La pluie du fichier est celle du pas écoulé : une heure, puis trois heures au-delà de l'horaire du modèle.
    hours = round((frame.valid - frame.origin).total_seconds() / 3600)
    span = 1.0 if hours <= source_icon.HOURLY_UNTIL.get(ctx.model, 10_000) else 3.0
    rate = np.zeros_like(pressure) if rain is None else rain / span
    native = source_icon.native_step(frame.raw("pressure_msl"))
    folder = os.path.join(ctx.out, FOLDER, f"{frame.valid:%Y%m%d%H}")
    return outils.write_tiles(lambda factor: sky_channels(pressure, rate, factor), native, folder)


def entry(ctx, times):
    return {
        "path": FOLDER + "/{time}/{level}/{row}-{col}.png",
        "times": [outils.format_time(valid) for valid in times],
        **ctx.provenance(model=ctx.model),
        "pressure": {"channels": "red,green", "low": PRESSURE_LOW, "quantum": 0.1, "unit": "hPa"},
        "rain": {"channel": "blue", "kind": "sqrt", "max": RAIN_MAX, "unit": "mm/h"},
    }


def prepare(ctx):
    return source_icon.prepare_alone(sys.modules[__name__], ctx)
