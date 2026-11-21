"""Famille des nuages par étage : la nébulosité basse, moyenne et haute d'ICON, tuiles n/.

Rouge : nuages bas ; vert : nuages moyens ; bleu : nuages hauts. Chaque canal de 0 à 100 %, pas de 0,4 (octet = % x 2,5),
comme la nébulosité de la famille de l'air. Valeurs de l'instant de la prévision (pas une moyenne de l'heure).

Étages d'ICON (définition du DWD, CLCL, CLCM et CLCH) : bas jusqu'à environ 800 hPa (2 km), moyens de 800 à 400 hPa
(environ 2 à 7 km), hauts au-dessus de 400 hPa. Une heure sans l'un des trois étages, ou avec des valeurs manquantes, est
laissée.
"""
import os
import sys

import numpy as np

import outils
import source_icon

NAME = "nuages"
LAYER = "clouds"  # sa clé dans index["layers"]
FOLDER = "n"
FIELDS = ("cloud_cover_low", "cloud_cover_mid", "cloud_cover_high")  # rouge, vert, bleu
SCALE = 2.5  # octet = pourcentage x 2,5


def cloud_byte(cover, factor):
    """Un étage, de 0 à 100 %, sur un octet (pas de 0,4 %)."""
    return np.round(np.clip(outils.coarser(cover, factor), 0.0, 100.0) * SCALE).astype(np.uint8)


def cloud_channels(low, mid, high, factor):
    """Rouge : nuages bas ; vert : moyens ; bleu : hauts."""
    return [cloud_byte(low, factor), cloud_byte(mid, factor), cloud_byte(high, factor)]


def write_hour(ctx, frame):
    fields = [source_icon.complete_field(frame, name) for name in FIELDS]
    if any(field is None for field in fields):
        return None
    native = source_icon.native_step(frame.raw(FIELDS[0]))
    folder = os.path.join(ctx.out, FOLDER, f"{frame.valid:%Y%m%d%H}")
    return outils.write_tiles(lambda factor: cloud_channels(*fields, factor), native, folder)


def entry(ctx, times):
    return {
        "path": FOLDER + "/{time}/{level}/{row}-{col}.png",
        "times": [outils.format_time(valid) for valid in times],
        **ctx.provenance(model=ctx.model),
        "low": {"channel": "red", "scale": SCALE, "unit": "%"},
        "mid": {"channel": "green", "scale": SCALE, "unit": "%"},
        "high": {"channel": "blue", "scale": SCALE, "unit": "%"},
        "moment": "instant",
    }


def prepare(ctx):
    return source_icon.prepare_alone(sys.modules[__name__], ctx)
