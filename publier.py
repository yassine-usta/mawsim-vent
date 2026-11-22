"""Publie le site que prepare.py a écrit : étape distincte de la préparation, avec ses propres jobs dans vent.yml.

Usage : python publier.py SITE --cible github-pages
        python publier.py SITE --cible cloudflare [--dossier ENVOI] [--sans-envoi]
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
  - Une famille nouvelle a son Worker d'elle-même ("mawsim-<clé d'index>"). Garde : une famille de plus de
    MARGE_FICHIERS fichiers (15 000, sous les 20 000 permis) arrête tout avant le premier envoi. Ce jour-là, on la
    découpera par niveaux (README, "Publication").
  - Envoi par wrangler (figé par cloudflare/package-lock.json) : les familles d'abord, l'index en dernier, toujours.
    Chaque Worker de famille sert un fichier empreinte.json (ses tuiles, ses en-têtes, sa configuration) ; une famille
    dont le Worker sert déjà la même empreinte n'est pas renvoyée (le feu dans la journée), et une empreinte illisible
    fait envoyer. Si l'envoi d'une famille échoue (le vent
    compris), l'index publié garde son entrée en ligne, que son Worker sert encore : l'index ne cite jamais une heure
    absente, sauf pendant les minutes de l'envoi, et seulement des heures déjà passées.
  - Familles faites une fois (par exemple un relief, dans son propre Worker, envoyé seulement quand on le refait) : leur
    entrée d'index porte "kept": true, et chaque passage la reprend de l'index en ligne sans toucher à leur Worker.
    Leur publication elle-même reste à écrire.
  - Variables d'environnement : CLOUDFLARE_API_TOKEN et CLOUDFLARE_ACCOUNT_ID (lues par wrangler), et
    CLOUDFLARE_SOUS_DOMAINE (par exemple "exemple.workers.dev") pour les adresses des Workers. Aucune n'est écrite
    dans ce dépôt public.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
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
MARGE_FICHIERS = 15000  # garde : de la place pour le pas horaire de 78 h et les familles qui grossissent
LIMITE_OCTETS = 25 * 1024 * 1024
DATE_COMPATIBILITE = "2026-11-01"
# wrangler et ses dépendances sont figés par cloudflare/package-lock.json, installés par `npm ci --prefix cloudflare`.
DOSSIER_OUTILS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloudflare")
WRANGLER_DELAI = 900  # secondes : un envoi bloqué compte comme un échec
AGENT = "mawsim-atelier"  # workers.dev refuse les clients sans nom (contrôle d'intégrité du navigateur)
SOUS_DOMAINE_ESSAI = "exemple.workers.dev"
EMPREINTE = "empreinte.json"  # dans chaque Worker de famille : ce qu'il sert, relu avant de décider d'un envoi
# En-têtes des fichiers servis (_headers). Les tuiles gardent leur chemin d'un calcul à l'autre : dix minutes, comme
# GitHub Pages aujourd'hui. L'index change à chaque calcul : une minute.
EN_TETES_TUILES = "/*\n  ! Cache-Control\n  Cache-Control: public, max-age=600\n"
EN_TETES_INDEX = "/index.json\n  ! Cache-Control\n  Cache-Control: public, max-age=60\n"
# Ce que wrangler écrit quand Cloudflare refuse le jeton : inutile d'essayer les Workers suivants.
REFUS = re.compile(r"Authentication error|Unable to authenticate|Invalid access token|code: (10000|10001|9106|9109)\]")
ADRESSE = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")


class RefusAuthentification(Exception):
    """Cloudflare refuse le jeton : plus aucun envoi dans ce passage."""


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
    """L'empreinte des tuiles d'une famille : leurs chemins et leur contenu. Même empreinte, mêmes tuiles."""
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


def worker_config(worker):
    return {"name": worker, "compatibility_date": DATE_COMPATIBILITE, "workers_dev": True, "preview_urls": False,
            "assets": {"directory": "./public"}}


def write_worker(folder, worker, headers):
    """Le dossier d'envoi d'un Worker à fichiers statiques seuls : public/ (servi tel quel) et wrangler.json à côté."""
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    os.makedirs(os.path.join(folder, "public"))
    with open(os.path.join(folder, "public", "_headers"), "w", encoding="utf-8", newline="\n") as out:
        out.write(headers)
    with open(os.path.join(folder, "wrangler.json"), "w", encoding="utf-8") as out:
        json.dump(worker_config(worker), out, indent=1)


def deployment_fingerprint(tiles, worker, headers):
    """L'empreinte de ce que sert un Worker de famille : ses tuiles, ses en-têtes et sa configuration."""
    text = json.dumps({"tiles": tiles, "headers": headers, "config": worker_config(worker)}, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def bundle_family(site, envoi, key, entry, suffix):
    """Range une famille dans le dossier de son Worker ; rend sa description. Lève ValueError si elle dépasse la marge
    ou les limites de Cloudflare."""
    worker = worker_name(key)
    label = WORKERS.get(key, key)
    folder = family_folder(entry)
    files = family_files(site, folder)
    if not files:
        raise ValueError(f"{label} : aucun fichier dans {folder}/")
    if len(files) + 2 > MARGE_FICHIERS:  # + _headers et empreinte.json
        raise ValueError(f"{label} : {len(files)} fichiers, plus que la marge de {MARGE_FICHIERS} sous les {LIMITE_FICHIERS} "
                         "d'un Worker ; la découper par niveaux (README, \"Publication\")")
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
    tiles = fingerprint(site, files)
    deployed = deployment_fingerprint(tiles, worker, EN_TETES_TUILES)
    with open(os.path.join(target, "public", EMPREINTE), "w", encoding="utf-8") as out:
        json.dump({"empreinte": deployed, "tuiles": tiles}, out)
    return {"key": key, "label": label, "worker": worker, "folder": target, "files": len(files), "bytes": total,
            "base": base_url(worker, suffix), "fingerprint": tiles, "deployed": deployed}


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


def read_json(url, timeout=30):
    request = urllib.request.Request(url, headers={"User-Agent": AGENT, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def fetch_live(suffix, read=read_json):
    """L'index en ligne sur Cloudflare, ou None (premier passage, ou injoignable)."""
    try:
        return read(base_url(INDEX_WORKER, suffix) + "index.json")
    except Exception as error:  # premier passage, ou Cloudflare injoignable
        print(f"Index en ligne illisible ({type(error).__name__}) : pas d'entrée à garder en cas d'échec.", flush=True)
        return None


def served_fingerprint(base, read=read_json):
    """L'empreinte que sert le Worker d'une famille, ou None (Worker absent, ancien, ou injoignable : on envoie)."""
    try:
        value = read(base + EMPREINTE).get("empreinte")
        return value if isinstance(value, str) else None
    except Exception:
        return None


def wrangler_command(folder):
    name = "wrangler.cmd" if os.name == "nt" else "wrangler"
    return [os.path.join(DOSSIER_OUTILS, "node_modules", ".bin", name), "deploy", "--config", os.path.join(folder, "wrangler.json")]


def wrangler_deploy(folder, tries=2, run=subprocess.run):
    """Envoie le dossier d'un Worker par le wrangler figé ; rend True s'il est en ligne. Un second essai après un échec,
    aucun après un refus du jeton (RefusAuthentification). Le journal de wrangler est réduit aux avertissements
    (WRANGLER_LOG=warn : sur un refus, wrangler afficherait sinon le nom du compte), et les adresses électroniques qui
    resteraient sont masquées : les journaux d'Actions de ce dépôt sont publics."""
    command = wrangler_command(folder)
    if not os.path.isfile(command[0]):
        print(f"wrangler absent ({command[0]}) : lancer d'abord `npm ci --prefix cloudflare`.", flush=True)
        return False
    env = {**os.environ, "WRANGLER_LOG": "warn", "WRANGLER_SEND_METRICS": "false", "CI": "true"}
    for attempt in range(1, tries + 1):
        started = time.time()
        try:
            result = run(command, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=WRANGLER_DELAI, check=False)
        except subprocess.TimeoutExpired:
            print(f"  wrangler bloqué plus de {WRANGLER_DELAI} s (essai {attempt} sur {tries})", flush=True)
            continue
        output = ADRESSE.sub("[adresse masquée]", (result.stdout or "") + (result.stderr or ""))
        if output.strip():
            print(output.rstrip(), flush=True)
        if result.returncode == 0:
            print(f"  en ligne en {time.time() - started:.0f} s", flush=True)
            return True
        if REFUS.search(output):
            raise RefusAuthentification(os.path.basename(folder))
        print(f"  wrangler a échoué en {time.time() - started:.0f} s (essai {attempt} sur {tries})", flush=True)
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


def publish_families(families, index, live, deploy, read):
    """Envoie les familles et tient leur entrée à jour dans [index]. Rend les noms des familles en échec, et si l'index
    reste publiable (faux seulement si le vent échoue sans entrée en ligne à garder). Laisse passer
    RefusAuthentification."""
    failures, publishable = [], True
    for family in families:
        key = family["key"]
        if served_fingerprint(family["base"], read) == family["deployed"]:
            print(f"{family['worker']} : inchangé (d'après l'empreinte qu'il sert), pas renvoyé", flush=True)
            continue
        print(f"{family['worker']} : envoi de {family['files']} fichiers ({family['bytes'] / 1048576:.0f} Mio)", flush=True)
        if deploy(family["folder"]):
            continue
        failures.append(family["label"])
        online = get_entry(live, key)
        if online:
            print(f"{family['worker']} : échec, l'index garde l'entrée en ligne", flush=True)
            set_entry(index, key, online)
        elif key is None:
            publishable = False
        else:
            print(f"{family['worker']} : échec, famille retirée de l'index", flush=True)
            index.get("layers", {}).pop(key, None)
    return failures, publishable


def cloudflare(site, options=None, deploy=wrangler_deploy, live=None, read=read_json):
    """Range le site par Worker dans options.dossier, puis l'envoie (sauf avec options.sans_envoi). Rend 0 si tout est
    en ligne, 1 sinon. [deploy], [live] et [read] servent aux essais, sans réseau."""
    options = options or argparse.Namespace(dossier="cloudflare-envoi", sans_envoi=True)
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
        live = fetch_live(suffix, read)
    carried = carry_kept(index, live)
    print_report(families, carried)

    if options.sans_envoi:
        write_index(envoi, index)
        print(f"Dossier d'envoi prêt, rien n'est envoyé : {envoi}")
        return 0
    started = time.time()
    try:
        failures, publishable = publish_families(families, index, live, deploy, read)
        if not publishable:
            print("Le vent n'a pas pu être envoyé, et aucun index en ligne ne dit ce que sert son Worker : l'index n'est pas "
                  "renvoyé.")
            return 1
        if not deploy(write_index(envoi, index)):
            print("L'index n'a pas pu être envoyé : l'app lit encore celui d'avant.")
            return 1
    except RefusAuthentification as refused:
        print(f"Cloudflare refuse le jeton ({refused}) : plus aucun envoi. L'index en ligne n'est pas touché ; vérifier "
              "CLOUDFLARE_API_TOKEN et CLOUDFLARE_ACCOUNT_ID.")
        return 1
    print(f"Envoi terminé en {time.time() - started:.0f} s.")
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
    args = parser.parse_args(argv)

    problems = check_site(args.site)
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
