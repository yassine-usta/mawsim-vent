"""Atelier de Mawsim : prépare les tuiles de valeurs que l'app télécharge pour sa carte.

Sources : les grilles publiques d'Open-Meteo (dépôt AWS "openmeteo", dossier data_spatial, licence CC BY 4.0), modèle
ICON du DWD par défaut ; les modèles océaniques de Météo-France ; le danger de feu du GWIS de Copernicus.

Chaque grandeur est une FAMILLE, un module du dossier familles/ qui écrit ses tuiles PNG (181 x 181 points, le nord
en haut, trois finesses ou quatre pour la mer et le feu, voir outils.py) et rend son entrée d'index :

  vent      vent à 10 m et rafales d'ICON (t/)               racine de l'index
  air       température et nébulosité d'ICON (a/)            layers.air
  ciel      pression et pluie d'ICON (s/)                    layers.sky
  eau       température de l'eau, Météo-France (m/)          layers.sea
  courants  courants de surface, Météo-France (c/)           layers.current
  feu       danger de feu, GWIS, une fois par jour (f/)      layers.fire

La liste des familles est FAMILIES, ci-dessous : ajouter une famille, c'est écrire son module et ajouter son nom
à cette liste (README.md, "Ajouter une famille"). Les familles qui lisent le fichier horaire d'ICON (vent, air, ciel)
le partagent : un seul téléchargement par heure (source_icon.py).

Le fichier index.json décrit le tout ; il est écrit en dernier. Ce script prépare seulement : le site est écrit dans le
dossier SORTIE, et publier.py le publie (README.md, "Publication").

Usage : python prepare.py SORTIE [--model dwd_icon] [--cache DOSSIER] [--limit N] [--published URL] [--no-sea]
        [--no-fire] [--fire-cache DOSSIER] [--familles vent,air] [--received AAAA-MM-JJThh:mmZ]
"""
import argparse
import datetime as dt
import importlib
import json
import os
import shutil
import sys
import tempfile
import time

import outils
import source_icon
from contexte import Contexte
# Les outils communs restent disponibles sous le nom `prepare.xxx` : les anciens modules (mer.py, feu.py,
# masque_mer.py, test_feu.py) reçoivent ce module comme boîte à outils.
from outils import (CELLS, LEVELS, SOURCE, VERSION, coarser, download, encode_positive, encode_signed, encode_wide,  # noqa: F401
                    fetch_json, format_time, full_circle, parse_time, source_url, write_tiles)

# LA liste des familles, dans l'ordre où elles apparaissent dans l'index. Un nom de module de familles/.
FAMILIES = ("vent", "air", "ciel", "nuages", "precipitations", "soleil", "poussiere", "uv", "eau", "courants", "feu")
# Ce que suppriment les options --no-sea et --no-fire.
SEA = ("eau", "courants")
FIRE = ("feu",)


def load_families(names=None):
    """Les modules des familles [names] (par défaut toutes), dans l'ordre de FAMILIES. Un nom inconnu est une erreur."""
    known = list(FAMILIES)
    names = known if names is None else list(names)
    unknown = [name for name in names if name not in known]
    if unknown:
        raise SystemExit(f"famille inconnue : {', '.join(unknown)} (connues : {', '.join(known)})")
    modules = [importlib.import_module(f"familles.{name}") for name in known if name in names]
    check_families(modules)
    return modules


def check_families(modules):
    """Chaque module doit suivre l'interface (README.md, "Ajouter une famille") ; deux familles ne partagent ni
    nom ni place dans l'index."""
    seen, layers = set(), set()
    for module in modules:
        name = getattr(module, "NAME", None)
        if not name or not hasattr(module, "LAYER"):
            raise SystemExit(f"{module.__name__} : NAME et LAYER sont obligatoires")
        if name in seen or module.LAYER in layers:
            raise SystemExit(f"{module.__name__} : nom ou place dans l'index déjà pris ({name}, {module.LAYER})")
        if hasattr(module, "write_hour"):
            if not (hasattr(module, "entry") and callable(getattr(module, "prepare", None))):
                raise SystemExit(f"{module.__name__} : une famille horaire a write_hour, entry et prepare")
        elif not callable(getattr(module, "prepare", None)):
            raise SystemExit(f"{module.__name__} : il manque prepare(ctx)")
        seen.add(name)
        layers.add(module.LAYER)


def run_families(ctx, modules):
    """Prépare les familles et rend {nom: entrée d'index} pour celles qui ont pu l'être.

    Les familles horaires d'ICON passent d'abord, ensemble (un téléchargement par heure) ; les autres suivent, chacune
    pour soi. Une famille autonome qui échoue est laissée : le reste est publié quand même."""
    entries = {}
    hourly = [module for module in modules if hasattr(module, "write_hour")]
    if hourly:
        done = source_icon.run_hours(ctx, hourly)
        for module in hourly:
            if done[module.NAME]:
                entries[module.NAME] = module.entry(ctx, done[module.NAME])
    for module in modules:
        if module in hourly:
            continue
        try:
            entry = module.prepare(ctx)
        except Exception as error:
            print(f"  {module.NAME} : famille laissée ({error})", flush=True)
            continue
        if entry:
            entries[module.NAME] = entry
    return entries


def build_index(ctx, modules, entries, created=None):
    """L'index.json : le calcul (run), sa réception par l'atelier (received), la publication (created), puis la famille
    historique (le vent) à la racine et les autres sous "layers". On ajoute des champs, on n'en renomme pas : les
    versions de l'app déjà installées le lisent encore."""
    plan = ctx.shared.get("icon_plan")
    index = {"version": VERSION, "model": ctx.model, **ctx.provenance()}
    index["completed_from"] = format_time(plan.previous) if plan and plan.older else None
    index["created"] = format_time(created or dt.datetime.now(dt.timezone.utc))
    index["source"] = "Open-Meteo (CC BY 4.0), modèle ICON du DWD" if ctx.model == "dwd_icon" else f"Open-Meteo (CC BY 4.0), {ctx.model}"
    layers = {}
    for module in modules:
        if module.NAME not in entries:
            continue
        if module.LAYER is None:
            index.update(entries[module.NAME])
        else:
            layers[module.LAYER] = entries[module.NAME]
    index["layers"] = layers
    return index


def choose_families(only=None, no_sea=False, no_fire=False):
    names = list(FAMILIES if not only else [name.strip() for name in only.split(",") if name.strip()])
    if no_sea:
        names = [name for name in names if name not in SEA]
    if no_fire:
        names = [name for name in names if name not in FIRE]
    return names


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Prépare les tuiles de Mawsim.")
    parser.add_argument("out", help="dossier de sortie (le site à publier)")
    parser.add_argument("--model", default="dwd_icon")
    parser.add_argument("--cache", help="dossier où garder les fichiers téléchargés (essais)")
    parser.add_argument("--limit", type=int, default=0, help="nombre d'heures à préparer (essais) ; 0 : toutes")
    parser.add_argument("--published", help="adresse de l'index déjà publié : rien n'est refait si le calcul est le même")
    parser.add_argument("--no-sea", action="store_true", help="ne pas préparer la mer (essais)")
    parser.add_argument("--no-fire", action="store_true", help="ne pas préparer le danger de feu (essais)")
    parser.add_argument("--fire-cache", help="dossier où garder les tuiles du feu du jour d'une publication à l'autre")
    parser.add_argument("--familles", help="les seules familles à préparer, séparées par des virgules (essais) : "
                                           + ", ".join(FAMILIES) + ". Un site sans le vent n'est pas publiable.")
    parser.add_argument("--received", help="heure à laquelle le calcul a été vu complet chez Open-Meteo (AAAA-MM-JJThh:mmZ), "
                                           "quand celui qui lance l'atelier la connaît mieux que lui ; par défaut, maintenant")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    modules = load_families(choose_families(args.familles, args.no_sea, args.no_fire))
    started = time.time()
    latest = fetch_json(f"{SOURCE}/{args.model}/latest.json")
    if not latest.get("completed", False):
        print("Le dernier calcul n'est pas encore complet chez Open-Meteo : rien à faire.")
        return 2
    run = parse_time(latest["reference_time"])
    received = parse_time(args.received) if args.received else dt.datetime.now(dt.timezone.utc)
    if args.published:
        try:
            current = fetch_json(args.published, timeout=30)
            if current.get("version") == VERSION and current.get("model") == args.model and current.get("run") == format_time(run):
                print(f"Le calcul du {run:%d/%m à %H h} UTC est déjà publié : rien à faire.")
                return 2
        except Exception as error:  # premier passage, ou site injoignable : on prépare
            print(f"Index publié illisible ({error}) : on prépare.")

    if os.path.isdir(args.out):
        shutil.rmtree(args.out)
    os.makedirs(args.out)
    work = args.cache or tempfile.mkdtemp(prefix="vent-")
    os.makedirs(work, exist_ok=True)
    ctx = Contexte(out=args.out, work=work, model=args.model, latest=latest, run=run, received=received, limit=args.limit,
                   keep_files=bool(args.cache), fire_cache=args.fire_cache)
    entries = run_families(ctx, modules)
    index = build_index(ctx, modules, entries)
    if "vent" not in entries:
        print("Attention : ce site n'a pas le vent, il ne doit pas être publié.", flush=True)
    with open(os.path.join(args.out, "index.json"), "w", encoding="utf-8") as out:
        json.dump(index, out, ensure_ascii=False, indent=1)
    print(f"Terminé : {ctx.shared.get('hours', 0)} heures, {ctx.shared.get('tiles', 0)} tuiles, "
          f"{ctx.shared.get('weight', 0) / 1048576:.0f} Mo, en {time.time() - started:.0f} s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
