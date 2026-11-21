"""Famille du soleil : le rayonnement solaire global et l'ensoleillement d'ICON, tuiles r/.

- Rouge : rayonnement global au sol (direct plus diffus sur un plan horizontal, "shortwave_radiation" d'Open-Meteo), en
  W/m², par pas de 5 (octet = W/m² / 5, de 0 à 1275).
- Vert : ensoleillement, en minutes de soleil par heure, par pas de 0,25 (octet = minutes x 4, de 0 à 240).
- Bleu : 0, réservé.

Les deux grandeurs sont des moyennes du pas écoulé (une heure, puis trois au-delà de l'horaire du modèle), comme le
rayonnement d'Open-Meteo. Sur un pas de trois heures, l'ensoleillement est la moyenne par heure des trois, comptées une à
une (voir sunshine_per_hour).

ICON ne donne pas l'ensoleillement : il est calculé comme Open-Meteo le fait pour son "sunshine_duration"
(Zensun.calculateBackwardsSunshineDuration, dépôt open-meteo/open-meteo, Sources/App/Helper/Solar/SunRiseSet.swift).
Il y a soleil quand le rayonnement direct ramené à un plan perpendiculaire au soleil (DNI) dépasse 120 W/m² (définition de
l'OMM). Les valeurs étant des moyennes du pas, Open-Meteo ne coupe pas net à 120 W/m² : la part du pas ensoleillée monte
de 0 à 1 entre 60 et 180 W/m². Le DNI est le rayonnement direct horizontal divisé par la hauteur moyenne du soleil (sinus)
sur la partie du pas où il est levé, et l'ensoleillement ne dépasse jamais la durée du jour comprise dans le pas.

L'heure du calcul n'a pas de rayonnement dans le fichier : elle est laissée.
"""
import datetime as dt
import math
import os
import sys

import numpy as np

import outils
import source_icon

NAME = "soleil"
LAYER = "sun"  # sa clé dans index["layers"]
FOLDER = "r"
RADIATION_QUANTUM = 5.0  # W/m² par octet
MINUTES_SCALE = 4.0  # octet = minutes x 4
DNI_LOW, DNI_HIGH = 60.0, 180.0  # W/m² : la rampe d'Open-Meteo autour du seuil de l'OMM (120)


def sun_position(moment):
    """Déclinaison du soleil (degrés) et équation du temps (heures, positive quand le soleil est en avance) à [moment],
    en UTC. Formules de l'Almanach astronomique (précision de l'ordre du centième de degré et de quelques secondes)."""
    n = moment.timestamp() / 86400.0 + 2440587.5 - 2451545.0  # jours depuis le 1er janvier 2000 à 12 h TT (à peu près)
    mean_longitude = (280.460 + 0.9856474 * n) % 360.0
    anomaly = math.radians((357.528 + 0.9856003 * n) % 360.0)
    ecliptic = math.radians(mean_longitude + 1.915 * math.sin(anomaly) + 0.020 * math.sin(2 * anomaly))
    obliquity = math.radians(23.439 - 0.0000004 * n)
    right_ascension = math.degrees(math.atan2(math.cos(obliquity) * math.sin(ecliptic), math.cos(ecliptic))) % 360.0
    declination = math.degrees(math.asin(math.sin(obliquity) * math.sin(ecliptic)))
    equation = (mean_longitude - right_ascension + 180.0) % 360.0 - 180.0  # degrés
    return declination, equation / 15.0


def daylight_geometry(latitude, longitude, end, seconds):
    """La course du soleil sur le pas de [seconds] secondes qui finit à [end] (UTC) : (sinus moyen de sa hauteur sur la
    partie du pas où il est levé, durée de cette partie en secondes, masque des points où il ne se lève pas du pas).
    Portage du calcul de Zensun.calculateBackwardsSunshineDuration d'Open-Meteo."""
    declination, equation = sun_position(end)
    hour = (end - end.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds() / 3600.0
    t1 = math.radians(90.0 - declination)
    p1 = math.radians(-15.0 * (hour - 12.0 + equation))  # longitude du soleil à la fin du pas
    p10 = math.radians(-15.0 * (hour - seconds / 3600.0 - 12.0 + equation))  # et au début (p10 > p1)
    t0 = np.radians(90.0 - np.asarray(latitude, dtype=np.float64))
    p0 = np.radians(np.asarray(longitude, dtype=np.float64))
    p0 = np.where(p0 < p1 - math.pi, p0 + 2 * math.pi, p0)
    p0 = np.where(p0 > p1 + math.pi, p0 - 2 * math.pi, p0)
    with np.errstate(divide="ignore", invalid="ignore"):
        # Demi-durée du jour, en angle horaire : le soleil est levé quand |p - p0| < carg.
        arg = -(np.cos(t0) * math.cos(t1)) / (np.sin(t0) * math.sin(t1))
        carg = np.where(np.abs(arg) <= 1.0, np.arccos(np.clip(arg, -1.0, 1.0)), math.pi)
        sunrise, sunset = p0 + carg, p0 - carg
        # Nuit polaire (arg > 1) : Open-Meteo y compte un jour entier et prend alors le rayonnement direct pour le DNI ;
        # elle est comptée ici comme une nuit. Sans effet sur ICON, dont le rayonnement direct y est nul.
        night = (p10 < sunset) | (p1 > sunrise) | (arg > 1.0)
        start, stop = np.minimum(sunrise, p10), np.maximum(sunset, p1)  # le pas, réduit à la partie où le soleil est levé
        daylight = seconds * np.abs((start - stop) / (p10 - p1))
        # Hauteur moyenne du soleil (son sinus) sur cette partie : intégrale de cos(t0) cos(t1) + sin(t0) sin(t1) cos(p - p0).
        left = np.sin(t0) * math.sin(t1) * np.sin(start - p0) + start * np.cos(t0) * math.cos(t1)
        right = np.sin(t0) * math.sin(t1) * np.sin(stop - p0) + stop * np.cos(t0) * math.cos(t1)
        delta = start - stop
        mean_sine = (left - right) / np.where(delta < 0, np.minimum(-0.001, delta), np.maximum(0.001, delta))
    return mean_sine, daylight, night


def sunshine_seconds(direct, latitude, longitude, end, seconds):
    """Secondes de soleil sur le pas de [seconds] secondes qui finit à [end] (UTC), d'après le rayonnement direct
    horizontal moyen du pas [direct] (W/m²). [latitude] et [longitude] en degrés, de formes compatibles avec [direct]
    (par exemple une colonne et une ligne). Portage de Zensun.calculateBackwardsSunshineDuration d'Open-Meteo."""
    direct = np.asarray(direct, dtype=np.float64)
    mean_sine, daylight, night = daylight_geometry(latitude, longitude, end, seconds)
    with np.errstate(divide="ignore", invalid="ignore"):
        dni = np.where(mean_sine <= 0.0001, direct, direct / mean_sine)
        sunshine = np.minimum(np.maximum(dni - DNI_LOW, 0.0) / (DNI_HIGH - DNI_LOW) * daylight, daylight)
    sunshine = np.where(night | (direct <= 0.0), 0.0, sunshine)
    return np.where(np.isnan(direct), np.nan, sunshine)


def top_of_atmosphere(latitude, longitude, end, seconds):
    """Sinus moyen de la hauteur du soleil sur tout le pas (zéro la nuit) : le rayonnement au sommet de l'atmosphère, à
    la constante solaire près."""
    mean_sine, daylight, night = daylight_geometry(latitude, longitude, end, seconds)
    return np.where(night, 0.0, np.maximum(mean_sine, 0.0) * daylight / seconds)


def sunshine_per_hour(direct, latitude, longitude, end, hours):
    """Minutes de soleil par heure sur un pas de [hours] heures qui finit à [end], d'après son rayonnement direct moyen.

    Sur un pas d'une heure, c'est sunshine_seconds / 60. Sur un pas de plusieurs heures, Open-Meteo répartit d'abord le
    rayonnement sur chaque heure (en suivant la course du soleil) puis compte l'ensoleillement heure par heure : un seul
    calcul sur le pas entier ferait d'une heure de soleil rasant et de deux heures de plein soleil un pas moyen. Ici, la
    clarté du ciel (rapport au rayonnement au sommet de l'atmosphère) est tenue constante sur le pas : chaque heure reçoit
    sa part du rayonnement du pas selon la hauteur du soleil, puis l'ensoleillement de chaque heure est compté comme le
    fait Open-Meteo. Open-Meteo, lui, fait varier cette clarté d'un pas à l'autre (interpolation), ce qu'une heure seule
    ne permet pas de refaire."""
    if hours == 1:
        return sunshine_seconds(direct, latitude, longitude, end, 3600.0) / 60.0
    direct = np.asarray(direct, dtype=np.float64)
    ends = [end - dt.timedelta(hours=hours - 1 - k) for k in range(int(hours))]
    shares = [top_of_atmosphere(latitude, longitude, moment, 3600.0) for moment in ends]
    mean_share = sum(shares) / len(shares)
    total = 0.0
    with np.errstate(divide="ignore", invalid="ignore"):
        for moment, share in zip(ends, shares):
            hourly = np.where(mean_share > 0.0, direct * share / mean_share, direct)
            total = total + sunshine_seconds(hourly, latitude, longitude, moment, 3600.0)
    return total / hours / 60.0


def grid_coordinates(field, step):
    """Latitudes (colonne, du sud au nord) et longitudes (ligne, depuis -180) des points d'une grille complétée."""
    latitude = -90.0 + step * np.arange(field.shape[0], dtype=np.float64)[:, None]
    longitude = -180.0 + step * np.arange(field.shape[1], dtype=np.float64)[None, :]
    return latitude, longitude


def span_hours(ctx, frame):
    """Durée du pas que couvrent les moyennes de [frame] : une heure, puis trois au-delà de l'horaire du modèle."""
    hours = round((frame.valid - frame.origin).total_seconds() / 3600)
    return 1.0 if hours <= source_icon.HOURLY_UNTIL.get(ctx.model, 10_000) else 3.0


def sun_channels(radiation, minutes, factor):
    """Rouge : rayonnement global (W/m², pas de 5) ; vert : minutes de soleil par heure (pas de 0,25) ; bleu : 0."""
    red = np.round(np.clip(outils.coarser(radiation, factor), 0.0, 255 * RADIATION_QUANTUM) / RADIATION_QUANTUM)
    green = np.round(np.clip(outils.coarser(minutes, factor), 0.0, 60.0) * MINUTES_SCALE)
    return [red.astype(np.uint8), green.astype(np.uint8), np.zeros(red.shape, dtype=np.uint8)]


def write_hour(ctx, frame):
    direct = source_icon.complete_field(frame, "direct_radiation")
    diffuse = source_icon.complete_field(frame, "diffuse_radiation")
    if direct is None or diffuse is None:
        return None
    native = source_icon.native_step(frame.raw("direct_radiation"))
    span = span_hours(ctx, frame)
    latitude, longitude = grid_coordinates(direct, native)
    minutes = sunshine_per_hour(direct, latitude, longitude, frame.valid, span).astype(np.float32)
    radiation = direct + diffuse
    folder = os.path.join(ctx.out, FOLDER, f"{frame.valid:%Y%m%d%H}")
    return outils.write_tiles(lambda factor: sun_channels(radiation, minutes, factor), native, folder)


def entry(ctx, times):
    return {
        "path": FOLDER + "/{time}/{level}/{row}-{col}.png",
        "times": [outils.format_time(valid) for valid in times],
        **ctx.provenance(model=ctx.model),
        "radiation": {"channel": "red", "quantum": RADIATION_QUANTUM, "unit": "W/m²"},
        "sunshine": {"channel": "green", "scale": MINUTES_SCALE, "unit": "min/h"},
        "moment": "mean_of_previous_step",
    }


def prepare(ctx):
    return source_icon.prepare_alone(sys.modules[__name__], ctx)
