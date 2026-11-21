"""Essais sans réseau de publier.py : un site abîmé n'est jamais publié."""
import json
import os
import shutil

import pytest

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
    assert set(publier.CIBLES) == {"github-pages"}


def test_a_target_must_be_chosen():
    with pytest.raises(SystemExit):
        publier.main(["site"])
