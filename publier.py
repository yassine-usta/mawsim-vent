"""Publie le site que prepare.py a écrit : étape distincte de la préparation, avec son propre job dans vent.yml.

Usage : python publier.py SITE --cible github-pages
        python publier.py SITE --verifier        (vérifie seulement, ne publie nulle part)

L'étape fait deux choses :

  1. elle vérifie le site : index.json lisible, champs de la racine présents, et pour chaque famille décrite, les
     tuiles de ses premières et dernières heures présentes. Un site abîmé ou incomplet n'est jamais publié ;
  2. elle l'envoie vers la CIBLE choisie.

Les cibles sont dans CIBLES. Aujourd'hui, une seule : GitHub Pages, que publient les actions du job
"publier-pages" de vent.yml (upload-pages-artifact puis deploy-pages) : ce script n'y ajoute rien au dossier, les
tuiles et l'index publiés sont ceux de la préparation, octet pour octet.

Où brancher l'hébergement Cloudflare (couloir H), sans toucher à la préparation :
  - une fonction `cloudflare(site)` ici, qui regroupe les tuiles par famille et par heure (PMTiles) puis envoie le
    dossier par `wrangler` ;
  - une ligne dans CIBLES ;
  - un job "publier-cloudflare" dans vent.yml (il y a un modèle en commentaire), qui reprend l'artefact "site" du
    job "preparer", comme le fait "publier-pages".
"""
import argparse
import datetime as dt
import json
import os
import sys

# Champs que la racine de l'index doit avoir (format 1 : ceux que lit l'app).
ROOT_FIELDS = ("version", "model", "run", "created", "source", "encoding", "tile", "levels", "path", "times", "layers")


def tile_path(site, template, time_text, level=0, row=0, col=0):
    """Le fichier d'une tuile : le gabarit du chemin ("t/{time}/{level}/{row}-{col}.png") rempli pour cette heure."""
    stamp = dt.datetime.strptime(time_text[:16], "%Y-%m-%dT%H:%M").strftime("%Y%m%d%H")
    return os.path.join(site, *template.format(time=stamp, level=level, row=row, col=col).split("/"))


def check_family(site, label, entry, problems, root_levels=()):
    template, times = entry.get("path"), entry.get("times")
    if not template or not isinstance(times, list):
        problems.append(f"{label} : chemin ou heures absents de l'index")
        return
    if not times:
        problems.append(f"{label} : aucune heure")
        return
    levels = entry.get("levels") or root_levels  # l'air et le ciel n'ont pas de niveaux à eux : ceux du vent
    last_level = len(levels) - 1 if levels else 0
    for time_text in sorted({times[0], times[-1]}):
        for level in sorted({0, last_level}):
            path = tile_path(site, template, time_text, level)
            if not os.path.isfile(path):
                problems.append(f"{label} : tuile absente pour {time_text}, niveau {level} ({os.path.relpath(path, site)})")


def check_site(site):
    """Les défauts du site, en phrases ; liste vide s'il peut être publié."""
    index_path = os.path.join(site, "index.json")
    try:
        with open(index_path, encoding="utf-8") as source:
            index = json.load(source)
    except (OSError, ValueError) as error:
        return [f"index.json illisible ({error})"]
    problems = [f"index.json : champ {name} absent" for name in ROOT_FIELDS if name not in index]
    if problems:
        return problems
    if not isinstance(index["layers"], dict):
        return ["index.json : layers n'est pas un objet"]
    check_family(site, "vent", index, problems)
    for key, entry in index["layers"].items():
        check_family(site, key, entry, problems, index["levels"])
    return problems


def github_pages(site):
    """GitHub Pages est publié par les actions du workflow : rien à écrire dans le dossier."""


# Les cibles de publication : nom -> fonction(dossier du site). Une cible de plus = une ligne ici (voir plus haut).
CIBLES = {"github-pages": github_pages}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Vérifie et publie le site préparé par prepare.py.")
    parser.add_argument("site", help="dossier écrit par prepare.py")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--cible", choices=sorted(CIBLES), help="où publier")
    group.add_argument("--verifier", action="store_true", help="vérifier seulement")
    args = parser.parse_args(argv)

    problems = check_site(args.site)
    if problems:
        print("Le site n'est pas publiable :")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"Site vérifié : {args.site}")
    if args.cible:
        CIBLES[args.cible](args.site)
        print(f"Site prêt pour la cible {args.cible}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
