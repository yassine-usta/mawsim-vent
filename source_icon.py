"""Le fichier horaire d'ICON, partagé par les familles qui le lisent (vent, air, ciel).

ICON publie, pour chaque heure de prévision, un fichier mondial de 150 Mo environ qui contient toutes les grandeurs.
Le télécharger une fois par famille coûterait trois fois plus ; ce module le télécharge donc une seule fois par heure
et le prête à toutes les familles "horaires", l'une après l'autre, avant de l'effacer.

Une famille horaire est un module de familles/ qui expose, en plus de NAME et LAYER (voir README.md) :

    write_hour(ctx, frame)  écrit les tuiles de cette heure ; rend (nombre de tuiles, poids en octets), ou None si
                            l'heure ne peut pas servir à cette famille (valeurs manquantes) : elle est laissée
    entry(ctx, times)       rend l'entrée d'index de la famille, d'après les heures réellement préparées
    ESSENTIAL (facultatif)  vrai si le passage doit échouer quand moins de la moitié des heures a pu être préparée

et la famille lit dans [frame] les grandeurs qu'elle veut : frame.raw("temperature_2m") rend la grille, ou None si
cette heure ne l'a pas.
"""
import datetime as dt
import os
import time

import numpy as np
from omfiles import OmFileReader

from outils import full_circle

HOURLY_UNTIL = {"dwd_icon": 78}  # heures après le calcul où le modèle passe de l'heure aux trois heures
HOURLY_HOURS = 48  # heure par heure jusque-là, puis toutes les trois heures
LAST_HOUR = 180  # sept jours et demi


def wanted_times(run, valid_times, parse_time):
    """Heure par heure sur 48 h, puis toutes les trois heures jusqu'à sept jours et demi."""
    kept = []
    for text in valid_times:
        valid = parse_time(text)
        hours = round((valid - run).total_seconds() / 3600)
        if hours < 0 or hours > LAST_HOUR:
            continue
        if hours <= HOURLY_HOURS or hours % 3 == 0:
            kept.append(valid)
    return kept


def older_times(run, last):
    """Heures qui manquent au bout du calcul (ceux de 6 h et 18 h UTC s'arrêtent à cinq jours), à prendre dans le
    calcul d'avant, qui va plus loin : rend ce calcul et ses heures, de trois en trois."""
    previous = run - dt.timedelta(hours=6)
    end = previous + dt.timedelta(hours=LAST_HOUR)
    times = []
    valid = last + dt.timedelta(hours=3)
    while valid <= end:
        times.append(valid)
        valid += dt.timedelta(hours=3)
    return previous, times


def native_step(field):
    """La maille d'une grille ICON, en degrés (du pôle Sud au pôle Nord, un point à chaque bout)."""
    return 180.0 / (field.shape[0] - 1)


class Plan:
    """Les heures à préparer : [times] = (heure, calcul d'origine) ; [previous] et [older] : le calcul d'avant et les
    heures qu'on lui prend pour compléter le bout de la semaine."""

    def __init__(self, times, previous, older):
        self.times = times
        self.previous = previous
        self.older = older

    @property
    def from_run(self):
        return len(self.times) - len(self.older)


def plan(ctx):
    """Le plan du passage, calculé une fois (les familles horaires le partagent) d'après latest.json du modèle."""
    if "icon_plan" in ctx.shared:
        return ctx.shared["icon_plan"]
    run = ctx.run
    times = [(valid, run) for valid in wanted_times(run, ctx.latest["valid_times"], ctx.tools.parse_time)]
    # Le bout de la semaine que ce calcul ne couvre pas vient du calcul précédent : six heures plus vieux,
    # ce qui ne change presque rien à six ou sept jours d'échéance.
    previous, older = older_times(run, times[-1][0]) if times else (None, [])
    if ctx.limit:
        times = times[:ctx.limit]
        older = older[:1] if ctx.limit < 0 else []
    times += [(valid, previous) for valid in older]
    ctx.shared["icon_plan"] = Plan(times, previous, older)
    return ctx.shared["icon_plan"]


class Frame:
    """Une heure du modèle, ouverte : les familles y lisent leurs grandeurs."""

    def __init__(self, reader, valid, origin, model):
        self.reader = reader
        self.valid = valid  # l'heure de la prévision
        self.origin = origin  # le calcul dont vient le fichier (le plus récent, ou le précédent pour le bout de la semaine)
        self.model = model
        self._fields = {}

    def raw(self, name):
        """La grille de la grandeur [name] (du sud au nord, en float32), ou None si ce fichier ne l'a pas."""
        if name not in self._fields:
            try:
                variable = self.reader.get_child_by_name(name)
                self._fields[name] = np.asarray(variable.read_array((slice(None), slice(None))), dtype=np.float32)
            except ValueError:
                self._fields[name] = None
        return self._fields[name]


def complete_field(frame, name):
    """La grille de [name], complétée sur un tour de longitudes ; None si l'heure ne l'a pas ou si elle a des valeurs
    manquantes (une grandeur absente n'empêche pas les autres familles de l'heure)."""
    field = frame.raw(name)
    if field is None or np.isnan(field).any():
        return None
    return full_circle(field, native_step(field))


def prepare_alone(family, ctx):
    """Le `prepare(ctx)` d'une famille horaire prise seule : télécharge les heures pour elle et rend son entrée d'index
    (None si aucune heure n'a pu être préparée). Dans un passage complet, prepare.py partage plutôt les fichiers."""
    done = run_hours(ctx, [family])[family.NAME]
    return family.entry(ctx, done) if done else None


def open_reader(path):
    return OmFileReader.from_path(path)


def run_hours(ctx, families):
    """Prépare les heures du plan pour toutes les [families] horaires, un seul téléchargement par heure. Rend
    {nom de la famille: heures préparées}."""
    tools = ctx.tools
    schedule = plan(ctx)
    started = time.time()
    print(f"Modèle {ctx.model}, calcul du {ctx.run:%d/%m/%Y à %H h} UTC, {len(schedule.times)} heures à préparer"
          f" (dont {len(schedule.older)} du calcul précédent).", flush=True)
    done = {family.NAME: [] for family in families}
    for valid, origin in schedule.times:
        name = f"{valid:%Y%m%d%H}"
        local = os.path.join(ctx.work, f"{ctx.model}-{origin:%Y%m%d%H}-{name}.om")
        if not (ctx.keep_files and os.path.exists(local)):
            try:
                tools.download(tools.source_url(ctx.model, origin, valid), local)
            except Exception as error:
                if origin == ctx.run:
                    raise
                # Le calcul précédent n'est plus en ligne pour cette heure : la semaine s'arrête plus tôt, sans échec.
                print(f"  {valid:%d/%m %H h} : introuvable dans le calcul précédent ({error}), heure laissée", flush=True)
                continue
        count = size = 0
        try:
            reader = open_reader(local)
            try:
                frame = Frame(reader, valid, origin, ctx.model)
                for family in families:
                    result = family.write_hour(ctx, frame)
                    if result is not None:
                        done[family.NAME].append(valid)
                        count, size = count + result[0], size + result[1]
            finally:
                reader.close()
        finally:
            if not ctx.keep_files:
                os.remove(local)
        ctx.shared["tiles"] = ctx.shared.get("tiles", 0) + count
        ctx.shared["weight"] = ctx.shared.get("weight", 0) + size
        if count:
            print(f"  {valid:%d/%m %H h} : {count} tuiles, {size / 1024:.0f} Ko ({time.time() - started:.0f} s)", flush=True)
    for family in families:
        if getattr(family, "ESSENTIAL", False) and len(done[family.NAME]) < max(1, schedule.from_run // 2):
            raise SystemExit("Moins de la moitié des heures a pu être préparée : rien n'est publié.")
    ctx.shared["hours"] = max([len(times) for times in done.values()] or [0])
    return done
