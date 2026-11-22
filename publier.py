"""Publie le site que prepare.py a écrit : étape distincte de la préparation, avec ses propres jobs dans vent.yml.

Usage : python publier.py SITE --cible github-pages
        python publier.py SITE --cible cloudflare [--dossier ENVOI] [--sans-envoi]
        python publier.py SITE --cible cloudflare --gardees   (familles faites une fois, voir plus bas)
        python publier.py SITE --verifier        (vérifie seulement, ne publie nulle part)

L'étape fait deux choses :

  1. elle vérifie le site : index.json lisible, champs de la racine présents, et pour chaque famille décrite, les
     tuiles de ses premières et dernières heures présentes. Un site abîmé ou incomplet n'est jamais publié ;
  2. elle l'envoie vers la CIBLE choisie (CIBLES).

GitHub Pages : publié par les actions du job "publier-pages" de vent.yml (upload-pages-artifact puis deploy-pages) ;
ce script n'ajoute rien au dossier.

Cloudflare (fichiers statiques des Workers, offre gratuite) : un Worker par famille, plus un pour l'index.
  - Pourquoi pas un seul site : un Worker garde au plus 20 000 fichiers par version et le site en compte davantage ; et
    les fichiers statiques ne répondent pas aux requêtes partielles (Range), si bien qu'une archive de tuiles ne
    pourrait être lue que par du code, compté dans les 100 000 requêtes par jour de l'offre gratuite. Les tuiles
    restent donc des fichiers à part, servis gratuitement et sans plafond, une requête par tuile comme sur Pages.
  - Chaque famille vit dans le Worker "mawsim-<famille>" (WORKERS), sous le même chemin que sur Pages : l'adresse d'une
    tuile est le champ "base" de son entrée d'index suivi de son gabarit "path" rempli. Ces deux champs, "base" et
    "fingerprint" (l'empreinte des tuiles de la famille), ne sont ajoutés qu'à l'index publié sur Cloudflare.
  - Les tuiles publiées sont celles de la préparation, octet pour octet (liens durs ou copies du dossier du site).
  - Envoi par wrangler : les familles d'abord, l'index en dernier. Une famille dont l'empreinte n'a pas changé depuis
    l'index en ligne n'est pas renvoyée (le feu dans la journée). Si l'envoi d'une famille échoue, l'index garde son
    entrée en ligne (son Worker sert encore ces tuiles-là) ; si c'est le vent, l'index n'est pas renvoyé du tout.
  - Familles faites une fois (par exemple un relief) : leur entrée d'index porte "kept": true. Elles sont publiées une
    fois par `--gardees` (le dossier ne contient qu'elles) ; ensuite chaque passage reprend leur entrée de l'index en
    ligne sans toucher à leur Worker.
  - Variables d'environnement : CLOUDFLARE_API_TOKEN et CLOUDFLARE_ACCOUNT_ID (lues par wrangler), et
    CLOUDFLARE_SOUS_DOMAINE (par exemple "exemple.workers.dev") pour les adresses des Workers. Aucune n'est écrite
    dans ce dépôt public.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request

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


def read_index(site):
    """L'index du site, ou (None, défaut en phrase)."""
    try:
        with open(os.path.join(site, "index.json"), encoding="utf-8") as source:
            return json.load(source), None
    except (OSError, ValueError) as error:
        return None, f"index.json illisible ({error})"


def check_site(site):
    """Les défauts du site, en phrases ; liste vide s'il peut être publié."""
    index, problem = read_index(site)
    if problem:
        return [problem]
    problems = [f"index.json : champ {name} absent" for name in ROOT_FIELDS if name not in index]
    if problems:
        return problems
    if not isinstance(index["layers"], dict):
        return ["index.json : layers n'est pas un objet"]
    check_family(site, "vent", index, problems)
    for key, entry in index["layers"].items():
        check_family(site, key, entry, problems, index["levels"])
    return problems


def check_kept_site(site):
    """Un dossier de familles faites une fois (--gardees) : des couches seulement, toutes "kept", tuiles présentes."""
    index, problem = read_index(site)
    if problem:
        return [problem]
    layers = index.get("layers")
    if "path" in index or not isinstance(layers, dict) or not layers:
        return ["index.json : il faut des couches seulement (pas de vent à la racine)"]
    problems = [f"{key} : pas marquée \"kept\": true" for key, entry in layers.items() if not entry.get("kept")]
    for key, entry in layers.items():
        check_family(site, key, entry, problems)
    return problems


def github_pages(site, options=None):
    """GitHub Pages est publié par les actions du workflow : rien à écrire dans le dossier."""
    return 0


# ---------------------------------------------------------------------------------------------------------------------
# Cloudflare : un Worker à fichiers statiques par famille, plus un pour l'index (voir en tête du fichier).

# Le Worker de chaque famille, d'après sa place dans l'index (None : la racine, le vent). Les noms sont ceux des modules
# de familles/ (test_publier.py vérifie que la table les suit). Une famille absente de la table prend sa clé d'index.
WORKERS = {None: "vent", "air": "air", "sky": "ciel", "clouds": "nuages", "precipitation": "precipitations", "sun": "soleil",
           "dust": "poussiere", "uv": "uv", "sea": "eau", "current": "courants", "fire": "feu"}
PREFIXE = "mawsim-"
INDEX_WORKER = PREFIXE + "index"
# Limites des fichiers statiques des Workers, offre gratuite (https://developers.cloudflare.com/workers/platform/limits/).
LIMITE_FICHIERS = 20000
LIMITE_OCTETS = 25 * 1024 * 1024
DATE_COMPATIBILITE = "2026-11-01"
WRANGLER = ("npx", "--yes", "wrangler@4.166.0")  # 4.34 au moins pour la limite de 20 000 fichiers
AGENT = "mawsim-atelier"  # workers.dev refuse les clients sans nom (contrôle d'intégrité du navigateur)
SOUS_DOMAINE_ESSAI = "exemple.workers.dev"
# En-têtes des fichiers servis (_headers). Les tuiles gardent leur chemin d'un calcul à l'autre : dix minutes, comme
# GitHub Pages aujourd'hui. L'index change à chaque calcul : une minute.
EN_TETES_TUILES = "/*\n  ! Cache-Control\n  Cache-Control: public, max-age=600\n"
EN_TETES_INDEX = "/index.json\n  ! Cache-Control\n  Cache-Control: public, max-age=60\n"


def worker_name(key):
    return PREFIXE + WORKERS.get(key, str(key).lower().replace("_", "-"))


def base_url(worker, suffix):
    return f"https://{worker}.{suffix}/"


def family_folder(entry):
    """Le dossier des tuiles d'une famille : le premier élément de son gabarit ("t/{time}/..." : "t")."""
    return entry["path"].split("/")[0]


def site_families(index):
    """(clé, entrée) de chaque famille de l'index : None pour le vent (la racine), puis les couches."""
    found = [(None, index)] if "path" in index else []
    return found + list(index.get("layers", {}).items())


def family_files(site, folder):
    """Les fichiers d'une famille, en chemins relatifs au site (barres obliques), dans un ordre fixe."""
    found = []
    for directory, _, files in os.walk(os.path.join(site, folder)):
        for name in files:
            found.append(os.path.relpath(os.path.join(directory, name), site).replace(os.sep, "/"))
    return sorted(found)


def file_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(site, files):
    """L'empreinte d'une famille : ses chemins et le contenu de chaque fichier. Même empreinte, mêmes tuiles."""
    digest = hashlib.sha256()
    for relative in files:
        digest.update(f"{relative}\0{file_digest(os.path.join(site, *relative.split('/')))}\n".encode())
    return digest.hexdigest()[:32]


def place_file(source, target):
    """Le fichier du site dans le dossier d'un Worker : un lien dur (rien n'est copié) ou, à défaut, une copie."""
    os.makedirs(os.path.dirname(target), exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


def write_worker(folder, worker, headers):
    """Le dossier d'envoi d'un Worker à fichiers statiques seuls : public/ (servi tel quel) et wrangler.json à côté."""
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    os.makedirs(os.path.join(folder, "public"))
    with open(os.path.join(folder, "public", "_headers"), "w", encoding="utf-8", newline="\n") as out:
        out.write(headers)
    config = {"name": worker, "compatibility_date": DATE_COMPATIBILITE, "workers_dev": True, "preview_urls": False,
              "assets": {"directory": "./public"}}
    with open(os.path.join(folder, "wrangler.json"), "w", encoding="utf-8") as out:
        json.dump(config, out, indent=1)


def bundle_family(site, envoi, key, entry, suffix):
    """Range une famille dans le dossier de son Worker ; rend sa description. Lève ValueError si elle dépasse les
    limites de Cloudflare."""
    worker = worker_name(key)
    label = WORKERS.get(key, key)
    folder = family_folder(entry)
    files = family_files(site, folder)
    if not files:
        raise ValueError(f"{label} : aucun fichier dans {folder}/")
    if len(files) + 1 > LIMITE_FICHIERS:  # + _headers
        raise ValueError(f"{label} : {len(files)} fichiers, plus que les {LIMITE_FICHIERS} d'un Worker")
    total = 0
    for relative in files:
        size = os.path.getsize(os.path.join(site, *relative.split("/")))
        if size > LIMITE_OCTETS:
            raise ValueError(f"{label} : {relative} pèse {size} octets, plus que les 25 Mio permis par fichier")
        total += size
    target = os.path.join(envoi, worker)
    write_worker(target, worker, EN_TETES_TUILES)
    for relative in files:
        place_file(os.path.join(site, *relative.split("/")), os.path.join(target, "public", *relative.split("/")))
    return {"key": key, "label": label, "worker": worker, "folder": target, "files": len(files), "bytes": total,
            "base": base_url(worker, suffix), "fingerprint": fingerprint(site, files)}


def get_entry(index, key):
    """L'entrée d'une famille dans un index (pour le vent : les champs de la racine, sans layers)."""
    if not index:
        return None
    if key is None:
        return {name: value for name, value in index.items() if name != "layers"} if "path" in index else None
    return index.get("layers", {}).get(key)


def set_entry(index, key, entry):
    if key is None:
        index.update(entry)
    else:
        index.setdefault("layers", {})[key] = entry


def carry_kept(index, live):
    """Les familles faites une fois ("kept": true) de l'index en ligne et absentes du site : leur entrée est reprise."""
    carried = []
    for key, entry in ((live or {}).get("layers") or {}).items():
        if isinstance(entry, dict) and entry.get("kept") and key not in index.get("layers", {}):
            index.setdefault("layers", {})[key] = entry
            carried.append(key)
    return carried


def write_index(envoi, index):
    target = os.path.join(envoi, INDEX_WORKER)
    write_worker(target, INDEX_WORKER, EN_TETES_INDEX)
    with open(os.path.join(target, "public", "index.json"), "w", encoding="utf-8") as out:
        json.dump(index, out, ensure_ascii=False, indent=1)
    return target


def fetch_live(suffix, timeout=30):
    """L'index en ligne sur Cloudflare, ou None (premier passage, ou injoignable)."""
    url = base_url(INDEX_WORKER, suffix) + "index.json"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": AGENT, "Cache-Control": "no-cache"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except Exception as error:  # premier passage, ou Cloudflare injoignable : tout est envoyé
        print(f"Index en ligne illisible ({error}) : toutes les familles sont envoyées.", flush=True)
        return None


def wrangler_deploy(folder, tries=2):
    """Envoie le dossier d'un Worker par wrangler ; rend True s'il est en ligne. Un second essai après un échec."""
    command = list(WRANGLER) + ["deploy", "--config", os.path.join(folder, "wrangler.json")]
    executable = shutil.which(command[0]) or command[0]  # npx.cmd sous Windows
    for attempt in range(1, tries + 1):
        if subprocess.run([executable] + command[1:], check=False).returncode == 0:
            return True
        print(f"  wrangler a échoué (essai {attempt} sur {tries})", flush=True)
    return False


def print_report(families, carried):
    print(f"{'Worker':<26}{'fichiers':>10}{'Mio':>10}")
    for family in families:
        print(f"{family['worker']:<26}{family['files']:>10}{family['bytes'] / 1048576:>10.1f}")
    for key in carried:
        print(f"{worker_name(key):<26}  repris de l'index en ligne, pas renvoyé")
    total_files = sum(family["files"] for family in families)
    total_bytes = sum(family["bytes"] for family in families)
    print(f"{'total':<26}{total_files:>10}{total_bytes / 1048576:>10.1f}", flush=True)


def publish_families(families, index, live, deploy):
    """Envoie les familles et tient leur entrée à jour dans [index] ; rend les noms des familles en échec."""
    failures = []
    for family in families:
        key = family["key"]
        online = get_entry(live, key)
        if online and online.get("fingerprint") == family["fingerprint"] and online.get("base") == family["base"]:
            print(f"{family['worker']} : inchangé, pas renvoyé", flush=True)
            continue
        print(f"{family['worker']} : envoi de {family['files']} fichiers ({family['bytes'] / 1048576:.0f} Mio)", flush=True)
        if deploy(family["folder"]):
            continue
        failures.append(family["label"])
        if key is None:
            continue  # le vent : l'index ne sera pas renvoyé (voir cloudflare)
        if online:
            print(f"{family['worker']} : échec, l'index garde l'entrée en ligne", flush=True)
            set_entry(index, key, online)
        else:
            print(f"{family['worker']} : échec, famille retirée de l'index", flush=True)
            index.get("layers", {}).pop(key, None)
    return failures


def cloudflare(site, options=None, deploy=wrangler_deploy, live=None):
    """Range le site par Worker dans options.dossier, puis l'envoie (sauf avec options.sans_envoi). Rend 0 si tout est
    en ligne, 1 sinon. [deploy] et [live] servent aux essais, sans réseau."""
    options = options or argparse.Namespace(dossier="cloudflare-envoi", sans_envoi=True, gardees=False)
    suffix = os.environ.get("CLOUDFLARE_SOUS_DOMAINE", "").strip().strip("/")
    if not suffix:
        if not options.sans_envoi:
            print("CLOUDFLARE_SOUS_DOMAINE absent : les adresses des Workers sont inconnues, rien n'est envoyé.")
            return 1
        suffix = SOUS_DOMAINE_ESSAI
    index, problem = read_index(site)
    if problem:
        print(problem)
        return 1
    envoi = options.dossier
    os.makedirs(envoi, exist_ok=True)
    try:
        families = [bundle_family(site, envoi, key, entry, suffix) for key, entry in site_families(index)]
    except ValueError as error:
        print(f"Le site ne tient pas dans les limites de Cloudflare : {error}")
        return 1
    for family in families:
        set_entry(index, family["key"], {**get_entry(index, family["key"]), "base": family["base"], "fingerprint": family["fingerprint"]})

    if live is None and not options.sans_envoi:
        live = fetch_live(suffix)
    if options.gardees:
        # Les familles faites une fois s'ajoutent à l'index en ligne, qui doit donc être lisible.
        if not live:
            print("--gardees demande l'index en ligne : rien n'est envoyé.")
            return 1
        merged = json.loads(json.dumps(live))
        for key, entry in index["layers"].items():
            merged.setdefault("layers", {})[key] = entry
        index = merged
    carried = carry_kept(index, live)
    print_report(families, carried)

    if options.sans_envoi:
        write_index(envoi, index)
        print(f"Dossier d'envoi prêt, rien n'est envoyé : {envoi}")
        return 0
    failures = publish_families(families, index, live, deploy)
    if any(family["key"] is None and family["label"] in failures for family in families):
        print("Le vent n'a pas pu être envoyé : l'index en ligne reste celui d'avant.")
        return 1
    if not deploy(write_index(envoi, index)):
        print("L'index n'a pas pu être envoyé : l'app lit encore celui d'avant.")
        return 1
    if failures:
        print(f"Index en ligne, mais familles en échec (entrée d'avant gardée ou retirée) : {', '.join(failures)}")
        return 1
    print("Tout est en ligne sur Cloudflare.")
    return 0


# Les cibles de publication : nom -> fonction(dossier du site, options) qui rend 0 si tout va bien. Une cible de plus =
# une ligne ici.
CIBLES = {"github-pages": github_pages, "cloudflare": cloudflare}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Vérifie et publie le site préparé par prepare.py.")
    parser.add_argument("site", help="dossier écrit par prepare.py")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--cible", choices=sorted(CIBLES), help="où publier")
    group.add_argument("--verifier", action="store_true", help="vérifier seulement")
    parser.add_argument("--dossier", default="cloudflare-envoi", help="cloudflare : où ranger les Workers à envoyer")
    parser.add_argument("--sans-envoi", action="store_true", help="cloudflare : ranger sans rien envoyer (essais)")
    parser.add_argument("--gardees", action="store_true",
                        help="cloudflare : publier des familles faites une fois (\"kept\": true), ajoutées à l'index en ligne")
    args = parser.parse_args(argv)
    if args.gardees and args.cible != "cloudflare":
        parser.error("--gardees va avec --cible cloudflare")

    problems = check_kept_site(args.site) if args.gardees else check_site(args.site)
    if problems:
        print("Le site n'est pas publiable :")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"Site vérifié : {args.site}")
    if args.cible:
        code = CIBLES[args.cible](args.site, args)
        if code:
            return code
        print(f"Site prêt pour la cible {args.cible}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
