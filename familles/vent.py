"""Famille du vent : le vent à 10 m et les rafales d'ICON, tuiles t/ (rouge : vent vers l'est ; vert : vent vers le
nord ; bleu : rafales). C'est la famille historique : son entrée est la racine de l'index, pas un élément de "layers".

Les valeurs sont codées par une racine (fines près de zéro, voir outils.encode_signed et encode_positive). Une heure
qui a des valeurs manquantes dans le vent est écartée ; les autres heures partent quand même.
"""
import os
import sys

import numpy as np

import outils
import source_icon

NAME = "vent"
LAYER = None  # sa place dans l'index : la racine
ESSENTIAL = True  # moins de la moitié des heures préparées : rien n'est publié
FOLDER = "t"
U_MAX = 50.0  # m/s : au-delà, la valeur est plafonnée (180 km/h)
GUST_MAX = 70.0  # m/s (252 km/h)


def wind_channels(u, v, gust, factor):
    """Rouge : vent vers l'est ; vert : vent vers le nord ; bleu : rafales (le maximum de la zone)."""
    return [outils.encode_signed(outils.coarser(u, factor), U_MAX), outils.encode_signed(outils.coarser(v, factor), U_MAX),
            outils.encode_positive(outils.coarser(gust, factor, peak=True), GUST_MAX)]


def write_hour(ctx, frame):
    u = frame.raw("wind_u_component_10m")
    v = frame.raw("wind_v_component_10m")
    if u is None or v is None:
        raise ValueError("le fichier du modèle n'a pas le vent à 10 m")
    gust = frame.raw("wind_gusts_10m")
    if gust is None:
        # L'heure du calcul lui-même n'a pas de rafales (elles se mesurent sur l'heure écoulée) :
        # le vent moyen en tient lieu, jamais moins.
        gust = np.hypot(u, v)
    native = source_icon.native_step(u)
    u, v, gust = (outils.full_circle(a, native) for a in (u, v, gust))
    if np.isnan(u).any() or np.isnan(v).any():
        # Une heure incomplète ne doit pas partir dans l'app : on l'écarte, les autres restent.
        print(f"  {frame.valid:%d/%m %H h} : valeurs manquantes, heure écartée", flush=True)
        return None
    gust = np.where(np.isnan(gust), np.hypot(u, v), gust)
    folder = os.path.join(ctx.out, FOLDER, f"{frame.valid:%Y%m%d%H}")
    return outils.write_tiles(lambda factor: wind_channels(u, v, gust, factor), native, folder)


def entry(ctx, times):
    """Les champs de la racine de l'index que le vent décrit : codage, tuiles, niveaux, chemins, heures."""
    return {
        "encoding": {"kind": "sqrt", "wind_max": U_MAX, "gust_max": GUST_MAX, "red": "u", "green": "v", "blue": "gust"},
        "tile": {"cells": outils.CELLS, "north_up": True},
        "levels": [{"id": i, "step": step, "span": step * outils.CELLS, "rows": int(round(180 / (step * outils.CELLS))),
                    "cols": int(round(360 / (step * outils.CELLS)))} for i, step in enumerate(outils.LEVELS)],
        "path": FOLDER + "/{time}/{level}/{row}-{col}.png",
        "times": [outils.format_time(valid) for valid in times],
    }


def prepare(ctx):
    return source_icon.prepare_alone(sys.modules[__name__], ctx)
