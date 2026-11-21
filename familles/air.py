"""Famille de l'air : la température à 2 m et la nébulosité d'ICON, tuiles a/.

Rouge et vert : température sur deux octets (dixièmes de degré depuis -90 °C) ; bleu : nébulosité (0 à 100 %, pas
de 0,4). Une heure sans l'une des deux grandeurs, ou avec des valeurs manquantes, est laissée.
"""
import os
import sys

import numpy as np

import outils
import source_icon

NAME = "air"
LAYER = "air"  # sa clé dans index["layers"]
FOLDER = "a"
TEMPERATURE_LOW = -90.0  # °C


def air_channels(temperature, cloud, factor):
    """Rouge et vert : température sur deux octets (dixièmes de degré depuis -90 °C) ; bleu : nébulosité (0 à 100 %, pas de 0,4)."""
    high, low = outils.encode_wide(outils.coarser(temperature, factor), TEMPERATURE_LOW, 0.1)
    return [high, low, np.round(np.clip(outils.coarser(cloud, factor), 0.0, 100.0) * 2.5).astype(np.uint8)]


def write_hour(ctx, frame):
    temperature = source_icon.complete_field(frame, "temperature_2m")
    cloud = source_icon.complete_field(frame, "cloud_cover")
    if temperature is None or cloud is None:
        return None
    native = source_icon.native_step(frame.raw("temperature_2m"))
    folder = os.path.join(ctx.out, FOLDER, f"{frame.valid:%Y%m%d%H}")
    return outils.write_tiles(lambda factor: air_channels(temperature, cloud, factor), native, folder)


def entry(ctx, times):
    return {
        "path": FOLDER + "/{time}/{level}/{row}-{col}.png",
        "times": [outils.format_time(valid) for valid in times],
        **ctx.provenance(model=ctx.model),
        "temperature": {"channels": "red,green", "low": TEMPERATURE_LOW, "quantum": 0.1, "unit": "°C"},
        "cloud": {"channel": "blue", "scale": 2.5, "unit": "%"},
    }


def prepare(ctx):
    return source_icon.prepare_alone(sys.modules[__name__], ctx)
