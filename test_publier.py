"""Essais sans réseau de publier.py : un site abîmé n'est jamais publié ; la cible cloudflare publie les tuiles de la
préparation octet pour octet, l'index en dernier, et ne casse pas l'index en ligne quand un envoi échoue."""
import argparse
import json
import os
import shutil
import urllib.parse

import pytest

import prepare
import publier


@pytest.fixture
def site(small_site, tmp_path):
    """Une copie du petit site préparé, que chaque essai peut abîmer."""
    source, _ = small_site
    copy = str(tmp_path / "site")
    shutil.copytree(source, copy)
    return copy


def rewrite_index(site, change):
    path = os.path.join(site, "index.json")
    with open(path, encoding="utf-8") as source:
        index = json.load(source)
    change(index)
    with open(path, "w", encoding="utf-8") as out:
        json.dump(index, out)


def test_a_prepared_site_can_be_published(site):
    assert publier.check_site(site) == []
    assert publier.main([site, "--cible", "github-pages"]) == 0
    assert publier.main([site, "--verifier"]) == 0


def test_publishing_changes_nothing_in_the_folder(site):
    before = sorted((os.path.relpath(os.path.join(d, f), site), os.path.getsize(os.path.join(d, f))) for d, _, files in os.walk(site) for f in files)
    publier.main([site, "--cible", "github-pages"])
    after = sorted((os.path.relpath(os.path.join(d, f), site), os.path.getsize(os.path.join(d, f))) for d, _, files in os.walk(site) for f in files)
    assert before == after


def test_a_missing_index_is_refused(site):
    os.remove(os.path.join(site, "index.json"))
    assert "index.json illisible" in publier.check_site(site)[0]
    assert publier.main([site, "--cible", "github-pages"]) == 1


def test_an_unreadable_index_is_refused(site):
    with open(os.path.join(site, "index.json"), "w", encoding="utf-8") as out:
        out.write("{pas du json")
    assert publier.main([site, "--verifier"]) == 1


def test_a_missing_root_field_is_refused(site):
    rewrite_index(site, lambda index: index.pop("encoding"))
    assert publier.check_site(site) == ["index.json : champ encoding absent"]


def test_a_site_without_the_wind_is_refused(site):
    """Ce que donne `prepare.py --familles air` : pas de vent, pas de publication."""
    def drop_wind(index):
        for name in ("encoding", "tile", "levels", "path", "times"):
            del index[name]
    rewrite_index(site, drop_wind)
    assert publier.main([site, "--cible", "github-pages"]) == 1


def test_a_missing_tile_of_the_first_or_last_hour_is_refused(site):
    os.remove(os.path.join(site, "t", "2026112106", "0", "0-0.png"))
    problems = publier.check_site(site)
    assert len(problems) == 1 and "vent : tuile absente pour 2026-11-21T06:00Z, niveau 0" in problems[0]


def test_a_missing_tile_in_another_family_names_it(site):
    shutil.rmtree(os.path.join(site, "s", "2026112106", "2"))
    problems = publier.check_site(site)
    assert len(problems) == 1 and problems[0].startswith("sky : tuile absente")


def test_a_family_without_hours_is_refused(site):
    rewrite_index(site, lambda index: index["layers"]["air"].update(times=[]))
    assert publier.check_site(site) == ["air : aucune heure"]


def test_the_targets_are_listed():
    assert set(publier.CIBLES) == {"github-pages", "cloudflare"}


def test_a_target_must_be_chosen():
    with pytest.raises(SystemExit):
        publier.main(["site"])


# ---------------------------------------------------------------------------------------------------------------------
# Cible cloudflare (aucun réseau : l'envoi par wrangler et l'index en ligne sont remplacés).

SUFFIX = "essai.workers.dev"


@pytest.fixture
def suffix(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_SOUS_DOMAINE", SUFFIX)
    return SUFFIX


def options(tmp_path, sans_envoi=False):
    return argparse.Namespace(dossier=str(tmp_path / "envoi"), sans_envoi=sans_envoi)


class FakeWrangler:
    """Retient les Workers envoyés, dans l'ordre ; échoue pour ceux qu'on lui donne."""

    def __init__(self, failing=()):
        self.sent, self.failing = [], set(failing)

    def __call__(self, folder):
        name = os.path.basename(folder)
        self.sent.append(name)
        return name not in self.failing


def published_index(envoi):
    with open(os.path.join(envoi, publier.INDEX_WORKER, "public", "index.json"), encoding="utf-8") as source:
        return json.load(source)


def all_files(folder):
    return sorted(os.path.relpath(os.path.join(d, f), folder).replace(os.sep, "/") for d, _, files in os.walk(folder) for f in files)


def read_bytes(path):
    with open(path, "rb") as source:
        return source.read()


def level_grid(entry, index):
    """(lignes, colonnes) de chaque niveau d'une famille : ses niveaux, ou ceux du vent."""
    levels = entry.get("levels") or index["levels"]
    return [(int(round(180 / (level["step"] * 180))), int(round(360 / (level["step"] * 180)))) for level in levels]


def test_the_workers_follow_the_families():
    """La table des Workers suit les modules de familles/ : un Worker "mawsim-<nom du module>" par place dans l'index."""
    for module in prepare.load_families():
        assert publier.WORKERS[module.LAYER] == module.NAME
    assert publier.worker_name(None) == "mawsim-vent"
    assert publier.worker_name("wave") == "mawsim-wave"  # une famille nouvelle prend sa clé d'index


def test_cloudflare_round_trip_is_byte_for_byte(site, tmp_path, suffix):
    """Chaque fichier du site est servi par un seul Worker, au même chemin, avec les mêmes octets ; chaque tuile de
    l'index se retrouve en suivant base + path de son entrée ; les entrées ne gagnent que base et fingerprint."""
    envoi = options(tmp_path).dossier
    assert publier.main([site, "--cible", "cloudflare", "--dossier", envoi, "--sans-envoi"]) == 0
    index = published_index(envoi)

    served = {}
    for worker in os.listdir(envoi):
        public = os.path.join(envoi, worker, "public")
        for relative in all_files(public):
            if relative in ("_headers", "index.json"):
                continue
            assert relative not in served, f"{relative} servi par deux Workers"
            served[relative] = worker
            assert read_bytes(os.path.join(public, *relative.split("/"))) == read_bytes(os.path.join(site, *relative.split("/")))
    assert sorted(served) == [f for f in all_files(site) if f != "index.json"]

    with open(os.path.join(site, "index.json"), encoding="utf-8") as source:
        prepared = json.load(source)
    checked = 0
    for key, entry in publier.site_families(index):
        url = urllib.parse.urlsplit(entry["base"])
        worker = url.hostname.split(".")[0]
        assert (url.scheme, url.hostname, url.path) == ("https", f"{worker}.{SUFFIX}", "/")
        for time_text in entry["times"]:
            for level, (rows, cols) in enumerate(level_grid(entry, index)):
                for row in range(rows):
                    for col in range(cols):
                        relative = os.path.relpath(publier.tile_path(".", entry["path"], time_text, level, row, col), ".")
                        assert served.get(relative.replace(os.sep, "/")) == worker, f"{key} : {relative} n'est pas chez {worker}"
                        checked += 1
        before = prepared if key is None else prepared["layers"][key]
        after = {name: value for name, value in entry.items() if name not in ("base", "fingerprint", "layers")}
        assert after == {name: value for name, value in before.items() if name != "layers"}
    assert checked == len(served)  # chaque fichier servi est une tuile décrite par l'index


def test_cloudflare_writes_assets_only_workers(site, tmp_path, suffix):
    envoi = options(tmp_path).dossier
    assert publier.cloudflare(site, options(tmp_path, sans_envoi=True)) == 0
    assert sorted(os.listdir(envoi)) == ["mawsim-air", "mawsim-ciel", "mawsim-index", "mawsim-vent"]
    for worker in os.listdir(envoi):
        with open(os.path.join(envoi, worker, "wrangler.json"), encoding="utf-8") as source:
            config = json.load(source)
        assert config["name"] == worker
        assert config["assets"] == {"directory": "./public"} and "main" not in config
        assert config["workers_dev"] is True
        with open(os.path.join(envoi, worker, "public", "_headers"), encoding="utf-8") as source:
            headers = source.read()
        assert "! Cache-Control" in headers and "Cache-Control: public, max-age=" in headers


def test_cloudflare_sends_the_families_then_the_index(site, tmp_path, suffix):
    wrangler = FakeWrangler()
    assert publier.cloudflare(site, options(tmp_path), deploy=wrangler, live={}) == 0
    assert wrangler.sent[-1] == "mawsim-index"
    assert sorted(wrangler.sent[:-1]) == ["mawsim-air", "mawsim-ciel", "mawsim-vent"]


def test_an_unchanged_family_is_not_sent_again(site, tmp_path, suffix):
    publier.cloudflare(site, options(tmp_path), deploy=FakeWrangler(), live={})
    live = published_index(options(tmp_path).dossier)
    again = FakeWrangler()
    assert publier.cloudflare(site, options(tmp_path), deploy=again, live=live) == 0
    assert again.sent == ["mawsim-index"]


def test_a_changed_tile_changes_the_fingerprint(site, tmp_path, suffix):
    publier.cloudflare(site, options(tmp_path), deploy=FakeWrangler(), live={})
    live = published_index(options(tmp_path).dossier)
    tile = os.path.join(site, "a", "2026112106", "0", "0-0.png")
    data = bytearray(read_bytes(tile))
    data[-1] ^= 1
    os.remove(tile)  # le fichier est peut-être lié (lien dur) au dossier d'envoi : on en écrit un nouveau
    with open(tile, "wb") as out:
        out.write(data)
    again = FakeWrangler()
    assert publier.cloudflare(site, options(tmp_path), deploy=again, live=live) == 0
    assert again.sent == ["mawsim-air", "mawsim-index"]


def test_a_failed_layer_keeps_its_online_entry(site, tmp_path, suffix):
    publier.cloudflare(site, options(tmp_path), deploy=FakeWrangler(), live={})
    live = published_index(options(tmp_path).dossier)
    live["layers"]["air"]["fingerprint"] = "ancienne"
    live["layers"]["air"]["run"] = "2026-11-21T00:00Z"
    wrangler = FakeWrangler(failing={"mawsim-air"})
    assert publier.cloudflare(site, options(tmp_path), deploy=wrangler, live=live) == 1
    assert wrangler.sent[-1] == "mawsim-index"
    assert published_index(options(tmp_path).dossier)["layers"]["air"] == live["layers"]["air"]


def test_a_failed_layer_without_online_entry_leaves_the_index(site, tmp_path, suffix):
    wrangler = FakeWrangler(failing={"mawsim-ciel"})
    assert publier.cloudflare(site, options(tmp_path), deploy=wrangler, live={}) == 1
    layers = published_index(options(tmp_path).dossier)["layers"]
    assert "sky" not in layers and "air" in layers


def test_a_failed_wind_keeps_its_online_entry(site, tmp_path, suffix):
    """Le vent raté : l'index part quand même, avec les champs du vent en ligne (que sert encore son Worker) et les
    autres familles du nouveau calcul."""
    publier.cloudflare(site, options(tmp_path), deploy=FakeWrangler(), live={})
    live = published_index(options(tmp_path).dossier)
    live.update(run="2026-11-21T00:00Z", times=["2026-11-21T00:00Z"], fingerprint="ancienne", created="2026-11-21T04:00Z")
    live["layers"]["air"]["fingerprint"] = "ancienne"
    wrangler = FakeWrangler(failing={"mawsim-vent"})
    assert publier.cloudflare(site, options(tmp_path), deploy=wrangler, live=live) == 1
    assert wrangler.sent[-1] == "mawsim-index"
    index = published_index(options(tmp_path).dossier)
    assert (index["run"], index["times"], index["fingerprint"]) == ("2026-11-21T00:00Z", ["2026-11-21T00:00Z"], "ancienne")
    assert index["layers"]["air"]["run"] == "2026-11-21T06:00Z"  # l'air, envoyé, est celui du nouveau calcul


def test_a_failed_wind_without_online_entry_never_sends_the_index(site, tmp_path, suffix):
    wrangler = FakeWrangler(failing={"mawsim-vent"})
    assert publier.cloudflare(site, options(tmp_path), deploy=wrangler, live={}) == 1
    assert "mawsim-index" not in wrangler.sent


def test_a_failed_index_is_a_failure(site, tmp_path, suffix):
    assert publier.cloudflare(site, options(tmp_path), deploy=FakeWrangler(failing={"mawsim-index"}), live={}) == 1


def test_a_family_over_the_margin_sends_nothing(site, tmp_path, suffix, monkeypatch):
    monkeypatch.setattr(publier, "MARGE_FICHIERS", 10)
    wrangler = FakeWrangler()
    assert publier.cloudflare(site, options(tmp_path), deploy=wrangler, live={}) == 1
    assert wrangler.sent == []


def test_a_file_over_the_size_limit_sends_nothing(site, tmp_path, suffix, monkeypatch):
    monkeypatch.setattr(publier, "LIMITE_OCTETS", 100)
    wrangler = FakeWrangler()
    assert publier.cloudflare(site, options(tmp_path), deploy=wrangler, live={}) == 1
    assert wrangler.sent == []


def test_without_the_account_subdomain_nothing_is_sent(site, tmp_path, monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_SOUS_DOMAINE", raising=False)
    wrangler = FakeWrangler()
    assert publier.cloudflare(site, options(tmp_path), deploy=wrangler, live={}) == 1
    assert wrangler.sent == []
    # Sans envoi, une adresse d'exemple suffit.
    assert publier.cloudflare(site, options(tmp_path, sans_envoi=True)) == 0
    assert published_index(options(tmp_path).dossier)["base"] == f"https://mawsim-vent.{publier.SOUS_DOMAINE_ESSAI}/"


def kept_entry(base="https://mawsim-relief.essai.workers.dev/"):
    return {"path": "k/{time}/{level}/{row}-{col}.png", "times": ["2026-01-01T00:00Z"], "levels": [{"id": 0, "step": 1.0}],
            "kept": True, "base": base, "fingerprint": "relief"}


def test_a_kept_family_is_carried_from_the_online_index(site, tmp_path, suffix):
    live = {"run": "2026-11-21T00:00Z", "layers": {"relief": kept_entry(), "old": {"path": "o/{time}"}}}
    wrangler = FakeWrangler()
    assert publier.cloudflare(site, options(tmp_path), deploy=wrangler, live=live) == 0
    layers = published_index(options(tmp_path).dossier)["layers"]
    assert layers["relief"] == kept_entry()
    assert "old" not in layers  # une couche ordinaire disparue du site n'est pas reprise
    assert "mawsim-relief" not in wrangler.sent


def test_the_biggest_pass_fits_the_margin():
    """Au plus gros passage prévu (pas horaire de 78 h, sept jours et demi, feu sur dix jours), chaque famille tient
    sous la marge de 15 000 fichiers, elle-même sous les 20 000 d'un Worker."""
    assert publier.MARGE_FICHIERS < publier.LIMITE_FICHIERS == 20000
    icon_hours = 79 + len(range(81, 181, 3))  # 113 : de 0 à 78 h heure par heure, puis de trois en trois
    world = {"icon": 2 + 8 + 32, "cams": 2 + 8, "sea": 2 + 8 + 32 + 288}  # tuiles par heure, tous niveaux
    biggest = {
        "vent, air, ciel, nuages, précipitations, soleil": icon_hours * world["icon"],
        "uv (si CAMS passe aussi à 78 h)": (79 + len(range(81, 121, 3))) * world["cams"],
        "poussière": len(range(0, 121, 3)) * world["cams"],
        "eau (4 heures)": 4 * world["sea"],
        "courants (13 heures)": 13 * world["sea"],
        "feu (10 jours)": 10 * world["sea"],
    }
    for name, count in biggest.items():
        assert count + 1 <= publier.MARGE_FICHIERS, f"{name} : {count} fichiers"
    assert max(biggest.values()) == 4746
