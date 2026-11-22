"""Contrôle de vent.yml ("Y a-t-il un nouveau calcul ?") : faut-il préparer et publier ?

Usage (dans le workflow) : python controle.py --source URL --modele dwd_icon --index URL [--index URL...] [--forcer]
Écrit `publier=oui|non` et `vu=AAAA-MM-JJThh:mmZ` dans $GITHUB_OUTPUT (ou les affiche hors de GitHub).

- Le calcul en ligne est le PLUS ANCIEN des index donnés (Pages, et Cloudflare quand il est allumé) : un hébergement
  resté en retard (par exemple deploy-pages raté alors que Cloudflare a réussi) est rattrapé au passage suivant. Un
  index absent ou illisible compte comme "aucun".
- On publie si le passage est forcé, ou si le dernier calcul d'Open-Meteo est complet et n'est pas celui en ligne.
- `vu` (le champ "received" de l'index) : l'heure où l'atelier voit le calcul complet. Si ce calcul est déjà en ligne
  (passage forcé, relancé pour CAMS), c'est le "received" de l'index en ligne : le calcul n'a pas été vu plus tard.
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.request

AGENT = "mawsim-atelier"  # workers.dev refuse les clients sans nom
AUCUN = "aucun"


def read_json(url, timeout=30):
    request = urllib.request.Request(url, headers={"User-Agent": AGENT, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def read_or_none(url, read=read_json):
    try:
        return read(url)
    except Exception as error:  # premier passage, hébergement injoignable, réponse abîmée
        print(f"Index illisible ({type(error).__name__}) : compté comme {AUCUN}.")
        return None


def run_of(index):
    run = index.get("run") if isinstance(index, dict) else None
    return run if isinstance(run, str) and len(run) >= 16 else AUCUN


def oldest_run(indexes):
    """Le calcul en ligne partout : le plus ancien, ou "aucun" dès qu'un index manque."""
    runs = [run_of(index) for index in indexes]
    if not runs or AUCUN in runs:
        return AUCUN
    return min(runs)  # même forme partout ("2026-11-22T06:00Z") : l'ordre des textes est celui du temps


def decide(latest, indexes, forcer, now):
    """Rend (publier, vu, calcul, en ligne)."""
    complete = isinstance(latest, dict) and latest.get("completed") is True
    calcul = f"{latest['reference_time'][:16]}Z" if isinstance(latest, dict) and latest.get("reference_time") else AUCUN
    online = oldest_run(indexes)
    publish = forcer or (complete and calcul != AUCUN and calcul != online)
    seen = f"{now:%Y-%m-%dT%H:%MZ}"
    for index in indexes:
        if run_of(index) == calcul and isinstance(index.get("received"), str):
            seen = index["received"]
            break
    return publish, seen, calcul, online


def main(argv=None, read=read_json, now=None):
    parser = argparse.ArgumentParser(description="Faut-il préparer et publier un nouveau calcul ?")
    parser.add_argument("--source", required=True)
    parser.add_argument("--modele", required=True)
    parser.add_argument("--index", action="append", default=[], help="index publié (plusieurs fois : le plus ancien compte)")
    parser.add_argument("--forcer", action="store_true")
    args = parser.parse_args(argv)
    latest = read(f"{args.source}/{args.modele}/latest.json")  # sans lui, rien à décider : l'erreur fait échouer l'étape
    indexes = [read_or_none(url, read) for url in args.index]
    publish, seen, calcul, online = decide(latest, indexes, args.forcer, now or dt.datetime.now(dt.timezone.utc))
    print(f"Calcul chez Open-Meteo : {calcul} (complet : {latest.get('completed')}) ; en ligne partout : {online} ; "
          f"forcé : {args.forcer}")
    lines = [f"publier={'oui' if publish else 'non'}", f"vu={seen}"]
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as out:
            out.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
