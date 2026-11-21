"""Famille de l'indice UV : l'indice UV de CAMS, tuiles u/.

- Rouge : indice UV (`uv_index`, ciel réel), au dixième : `indice = octet / 10`, de 0 à 25,5.
- Vert : indice UV par ciel clair (`uv_index_clear_sky`), même codage.
- Bleu : 0, réservé.

Les valeurs viennent de CAMS (voir source_cams.py) : deux niveaux de tuiles (1° et 0,5°), les heures du vent à partir du
calcul d'ICON, et les dates du calcul de CAMS.
"""
import sys

import numpy as np

import outils
import source_cams

NAME = "uv"
LAYER = "uv"  # sa clé dans index["layers"]
FOLDER = "u"
VARIABLES = ("uv_index", "uv_index_clear_sky")
SCALE = 10.0  # octet = indice x 10


def encode_uv(values):
    return np.round(np.clip(np.asarray(values, dtype=np.float64), 0.0, 255.0 / SCALE) * SCALE).astype(np.uint8)


def channels(fields, factor):
    """Rouge : indice UV ; vert : indice UV par ciel clair ; bleu : 0."""
    uv = encode_uv(outils.coarser(fields["uv_index"], factor))
    clear = encode_uv(outils.coarser(fields["uv_index_clear_sky"], factor))
    return [uv, clear, np.zeros(uv.shape, dtype=np.uint8)]


def entry(ctx, cams, times):
    return {
        "path": FOLDER + "/{time}/{level}/{row}-{col}.png",
        "times": [outils.format_time(valid) for valid in times],
        "levels": source_cams.levels(),
        **cams.provenance(ctx),
        "source": "Open-Meteo (CC BY 4.0), CAMS global (Copernicus, ECMWF)",
        "uv": {"channel": "red", "scale": SCALE, "unit": "indice UV"},
        "uv_clear_sky": {"channel": "green", "scale": SCALE, "unit": "indice UV"},
        "moment": "instant",
    }


def prepare(ctx):
    return source_cams.prepare_family(ctx, sys.modules[__name__])
