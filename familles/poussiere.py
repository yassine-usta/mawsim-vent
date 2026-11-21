"""Famille de la poussière : la poussière au sol de CAMS (µg/m³), tuiles d/.

- Rouge : concentration de poussière près du sol (`dust` chez Open-Meteo, µg/m³), codée par un logarithme qui garde les
  quelques µg/m³ des journées calmes comme les tempêtes de plusieurs milliers :
  `valeur = REF x (exp(octet / 255 x ln(1 + MAX / REF)) - 1)`, avec REF = 1 µg/m³ et MAX = 10000 µg/m³. Deux octets
  voisins diffèrent de 3,7 % de (REF + valeur) : 0,07 µg/m³ vers 1 µg/m³, 1 % de la valeur vers 100, 3,7 % vers 3000.
- Vert : épaisseur optique des aérosols (`aerosol_optical_depth`, sans unité, toutes poussières et particules), racine :
  `valeur = (octet / 255)² x 5`.
- Bleu : 0, réservé.

Les valeurs viennent de CAMS (voir source_cams.py) : deux niveaux de tuiles (1° et 0,5°), les heures du vent à partir du
calcul d'ICON, et les dates du calcul de CAMS.
"""
import sys

import numpy as np

import outils
import source_cams

NAME = "poussiere"
LAYER = "dust"  # sa clé dans index["layers"]
FOLDER = "d"
THREE_HOURLY = True  # CAMS ne calcule la poussière que toutes les trois heures ; les fichiers des autres heures ne l'ont pas
VARIABLES = ("dust", "aerosol_optical_depth")
DUST_REF = 1.0  # µg/m³ : en dessous, le codage est presque linéaire
DUST_MAX = 10000.0  # µg/m³ : octet 255 (la tempête la plus forte vue dans CAMS dépasse à peine 3500 µg/m³)
AOD_MAX = 5.0


def encode_dust(values):
    """Concentration de poussière (µg/m³) en octet : logarithme de 1 + valeur / REF, de 0 à MAX."""
    clipped = np.clip(np.asarray(values, dtype=np.float64), 0.0, DUST_MAX)
    return np.round(255.0 * np.log1p(clipped / DUST_REF) / np.log1p(DUST_MAX / DUST_REF)).astype(np.uint8)


def decode_dust(octets):
    return DUST_REF * np.expm1(np.asarray(octets, dtype=np.float64) / 255.0 * np.log1p(DUST_MAX / DUST_REF))


def channels(fields, factor):
    """Rouge : poussière (log) ; vert : épaisseur optique (racine) ; bleu : 0."""
    dust = encode_dust(outils.coarser(fields["dust"], factor))
    aod = outils.encode_positive(outils.coarser(fields["aerosol_optical_depth"], factor), AOD_MAX)
    return [dust, aod, np.zeros(dust.shape, dtype=np.uint8)]


def entry(ctx, cams, times):
    return {
        "path": FOLDER + "/{time}/{level}/{row}-{col}.png",
        "times": [outils.format_time(valid) for valid in times],
        "levels": source_cams.levels(),
        **cams.provenance(ctx),
        "source": "Open-Meteo (CC BY 4.0), CAMS global (Copernicus, ECMWF)",
        "dust": {"channel": "red", "kind": "log1p", "ref": DUST_REF, "max": DUST_MAX, "unit": "µg/m³"},
        "aod": {"channel": "green", "kind": "sqrt", "max": AOD_MAX, "unit": "", "grandeur": "épaisseur optique des aérosols"},
        "moment": "instant",
    }


def prepare(ctx):
    return source_cams.prepare_family(ctx, sys.modules[__name__])
