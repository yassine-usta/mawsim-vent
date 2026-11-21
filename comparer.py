"""Compare deux sites préparés (l'ancien et le nouveau) : mêmes fichiers, mêmes octets, même index.

Usage : python comparer.py ANCIEN NOUVEAU

  - les tuiles (tous les fichiers autres que index.json) doivent être les mêmes, octet pour octet ;
  - l'index du nouveau doit garder tout champ de l'ancien, avec la même valeur, sauf "created" (l'heure de la
    création, qui change à chaque passage) ; les champs que le nouveau ajoute sont listés, pas refusés.

Rend 0 si le nouveau site remplace l'ancien sans rien changer de ce que lit l'app, 1 sinon.
"""
import argparse
import hashlib
import json
import os
import sys

IGNORED = ("created",)  # champs de la racine qui changent à chaque passage


def files_of(site):
    found = {}
    for folder, _, names in os.walk(site):
        for name in names:
            path = os.path.join(folder, name)
            found[os.path.relpath(path, site).replace(os.sep, "/")] = path
    return found


def digest(path):
    with open(path, "rb") as source:
        return hashlib.sha256(source.read()).hexdigest()


def diff_json(old, new, where="", ignored=()):
    """Les différences d'un objet JSON à l'autre : (champs retirés ou changés, champs ajoutés)."""
    changed, added = [], []
    if isinstance(old, dict) and isinstance(new, dict):
        for key, value in old.items():
            if key in ignored:
                continue
            if key not in new:
                changed.append(f"{where}{key} : retiré")
                continue
            sub_changed, sub_added = diff_json(value, new[key], f"{where}{key}.")
            changed += sub_changed
            added += sub_added
        added += [f"{where}{key}" for key in new if key not in old]
    elif old != new:
        changed.append(f"{where[:-1] or 'racine'} : {old!r} devient {new!r}")
    return changed, added


def compare(old_site, new_site):
    """Rend (ce qui diffère, ce qui est ajouté, nombre de tuiles identiques)."""
    old, new = files_of(old_site), files_of(new_site)
    problems = [f"fichier absent du nouveau : {name}" for name in sorted(set(old) - set(new))]
    problems += [f"fichier en trop dans le nouveau : {name}" for name in sorted(set(new) - set(old))]
    same = 0
    for name in sorted(set(old) & set(new)):
        if name == "index.json":
            continue
        if digest(old[name]) == digest(new[name]):
            same += 1
        else:
            problems.append(f"octets différents : {name}")
    added = []
    if "index.json" in old and "index.json" in new:
        with open(old["index.json"], encoding="utf-8") as source:
            old_index = json.load(source)
        with open(new["index.json"], encoding="utf-8") as source:
            new_index = json.load(source)
        changed, added = diff_json(old_index, new_index, ignored=IGNORED)
        problems += [f"index : {line}" for line in changed]
    return problems, added, same


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compare deux sites préparés.")
    parser.add_argument("ancien")
    parser.add_argument("nouveau")
    args = parser.parse_args(argv)
    problems, added, same = compare(args.ancien, args.nouveau)
    print(f"{same} tuiles identiques, octet pour octet.")
    print(f"Champs d'index ajoutés par le nouveau ({len(added)}) : {', '.join(added) or 'aucun'}")
    if problems:
        print(f"{len(problems)} différence(s) :")
        for problem in problems[:50]:
            print(f"  - {problem}")
        return 1
    print("Rien d'autre ne change : le nouveau site remplace l'ancien.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
