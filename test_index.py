"""Essais sans réseau de l'index.json : les champs anciens sont intacts, les champs nouveaux sont là."""
import datetime as dt
import glob
import json
import os
import re
import types

import numpy as np
import pytest

import feu
import mer
import outils
import prepare
from conftest import MODIFIED, RECEIVED, fake_latest, install_fake_icon

TIME = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\dZ$")

# L'index d'avant la refonte (main, commit 2eff857), champ par champ : l'app installée le lit encore.
OLD_ROOT = {
    "version": 1,
    "model": "dwd_icon",
    "run": "2026-11-21T06:00Z",
    "completed_from": None,
    "source": "Open-Meteo (CC BY 4.0), modèle ICON du DWD",
    "encoding": {"kind": "sqrt", "wind_max": 50.0, "gust_max": 70.0, "red": "u", "green": "v", "blue": "gust"},
    "tile": {"cells": 180, "north_up": True},
    "levels": [
        {"id": 0, "step": 1.0, "span": 180.0, "rows": 1, "cols": 2},
        {"id": 1, "step": 0.5, "span": 90.0, "rows": 2, "cols": 4},
        {"id": 2, "step": 0.25, "span": 45.0, "rows": 4, "cols": 8},
    ],
    "path": "t/{time}/{level}/{row}-{col}.png",
    "times": ["2026-11-21T06:00Z"],
}
OLD_LAYERS = {
    "air": {
        "path": "a/{time}/{level}/{row}-{col}.png",
        "times": ["2026-11-21T06:00Z"],
        "temperature": {"channels": "red,green", "low": -90.0, "quantum": 0.1, "unit": "°C"},
        "cloud": {"channel": "blue", "scale": 2.5, "unit": "%"},
    },
    "sky": {
        "path": "s/{time}/{level}/{row}-{col}.png",
        "times": ["2026-11-21T06:00Z"],
        "pressure": {"channels": "red,green", "low": 850.0, "quantum": 0.1, "unit": "hPa"},
        "rain": {"channel": "blue", "kind": "sqrt", "max": 60.0, "unit": "mm/h"},
    },
}


class TestChampsAnciens:
    def test_root_fields_are_unchanged(self, small_site):
        _, index = small_site
        for name, value in OLD_ROOT.items():
            assert index[name] == value, name

    def test_layers_fields_are_unchanged(self, small_site):
        _, index = small_site
        assert list(index["layers"]) == ["air", "sky"]
        for layer, old in OLD_LAYERS.items():
            for name, value in old.items():
                assert index["layers"][layer][name] == value, (layer, name)

    def test_old_fields_are_all_still_there_only_new_ones_were_added(self, small_site):
        _, index = small_site
        assert set(index) - set(OLD_ROOT) == {"received", "source_modified", "created", "layers"}
        assert set(index["layers"]["air"]) - set(OLD_LAYERS["air"]) == {"model", "run", "received", "source_modified"}
        assert set(index["layers"]["sky"]) - set(OLD_LAYERS["sky"]) == {"model", "run", "received", "source_modified"}

    def test_tile_paths_are_unchanged(self, small_site):
        site, _ = small_site
        files = sorted(os.path.relpath(p, site).replace(os.sep, "/") for p in glob.glob(os.path.join(site, "*", "*", "*", "*.png")))
        assert len(files) == 3 * (2 + 8 + 32)
        for folder in ("t", "a", "s"):
            assert f"{folder}/2026112106/0/0-0.png" in files
            assert f"{folder}/2026112106/1/1-3.png" in files
            assert f"{folder}/2026112106/2/3-7.png" in files

    def test_tile_size_and_format(self, small_site):
        from PIL import Image
        site, _ = small_site
        with Image.open(os.path.join(site, "t", "2026112106", "2", "0-0.png")) as image:
            assert image.mode == "RGB" and image.size == (181, 181)


class TestChampsNouveaux:
    def test_the_run_the_reception_and_the_publication_times(self, small_site):
        _, index = small_site
        assert index["run"] == "2026-11-21T06:00Z"
        assert index["received"] == RECEIVED
        assert index["source_modified"] == "2026-11-21T09:17Z"
        assert TIME.match(index["created"])

    def test_every_family_carries_its_run_and_its_reception(self, small_site):
        _, index = small_site
        for layer in ("air", "sky"):
            entry = index["layers"][layer]
            assert entry["run"] == index["run"]
            assert entry["received"] == RECEIVED
            assert entry["source_modified"] == "2026-11-21T09:17Z"
            assert entry["model"] == "dwd_icon"

    def test_every_time_has_the_index_format(self, small_site):
        _, index = small_site
        for value in (index["run"], index["received"], index["source_modified"], index["created"], *index["times"]):
            assert TIME.match(value), value

    def test_the_reception_defaults_to_now(self, tmp_path, monkeypatch):
        install_fake_icon(monkeypatch)
        before = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
        code = prepare.main([str(tmp_path / "site"), "--limit", "1", "--familles", "air", "--cache", str(tmp_path / "c")])
        with open(tmp_path / "site" / "index.json", encoding="utf-8") as source:
            index = json.load(source)
        assert code == 0
        assert before <= outils.parse_time(index["received"]) <= dt.datetime.now(dt.timezone.utc)

    def test_no_source_modified_when_the_server_does_not_say(self, tmp_path, monkeypatch):
        latest = fake_latest()
        del latest["last_modified_time"]
        install_fake_icon(monkeypatch, latest=latest)
        prepare.main([str(tmp_path / "site"), "--limit", "1", "--familles", "air", "--cache", str(tmp_path / "c")])
        with open(tmp_path / "site" / "index.json", encoding="utf-8") as source:
            index = json.load(source)
        assert "source_modified" not in index and "source_modified" not in index["layers"]["air"]


class TestPassage:
    def test_nothing_is_done_while_the_run_is_incomplete(self, tmp_path, monkeypatch, capsys):
        install_fake_icon(monkeypatch, latest=fake_latest(completed=False))
        assert prepare.main([str(tmp_path / "site"), "--limit", "1"]) == 2
        assert not os.path.exists(tmp_path / "site")
        assert "pas encore complet" in capsys.readouterr().out

    def test_nothing_is_done_when_the_run_is_already_published(self, tmp_path, monkeypatch, capsys):
        latest = fake_latest()
        published = {"version": 1, "model": "dwd_icon", "run": "2026-11-21T06:00Z"}
        monkeypatch.setattr(prepare, "fetch_json", lambda url, timeout=60: published if url.endswith("index.json") else latest)
        assert prepare.main([str(tmp_path / "site"), "--published", "https://exemple/index.json"]) == 2
        assert not os.path.exists(tmp_path / "site")
        assert "déjà publié" in capsys.readouterr().out

    def test_familles_option_prepares_only_those(self, tmp_path, monkeypatch):
        install_fake_icon(monkeypatch)
        site = tmp_path / "site"
        assert prepare.main([str(site), "--limit", "1", "--familles", "air", "--cache", str(tmp_path / "c")]) == 0
        with open(site / "index.json", encoding="utf-8") as source:
            index = json.load(source)
        assert sorted(os.listdir(site)) == ["a", "index.json"]
        assert list(index["layers"]) == ["air"]
        assert "encoding" not in index and "path" not in index  # sans le vent : pas publiable

    def test_a_site_without_wind_says_so(self, tmp_path, monkeypatch, capsys):
        install_fake_icon(monkeypatch)
        prepare.main([str(tmp_path / "site"), "--limit", "1", "--familles", "ciel", "--cache", str(tmp_path / "c")])
        assert "n'a pas le vent" in capsys.readouterr().out

    def test_old_options_still_exist(self):
        args = prepare.parse_args(["site", "--model", "m", "--cache", "c", "--limit", "2", "--published", "u", "--no-sea",
                                   "--no-fire", "--fire-cache", "f"])
        assert (args.model, args.cache, args.limit, args.published, args.no_sea, args.no_fire, args.fire_cache) == (
            "m", "c", 2, "u", True, True, "f")
        assert prepare.parse_args(["site"]).limit == 0

    def test_the_toolbox_is_still_reachable_under_the_old_names(self):
        for name in ("encode_wide", "encode_signed", "encode_positive", "write_tiles", "fetch_json", "parse_time", "source_url",
                     "download", "SOURCE", "VERSION"):
            assert getattr(prepare, name) is getattr(outils, name)


class TestFamillesAutonomes:
    def fake_tools(self, latest):
        return types.SimpleNamespace(
            SOURCE="https://exemple", fetch_json=lambda url: latest, parse_time=outils.parse_time, source_url=lambda *a: "u",
            download=lambda url, path: None, write_tiles=lambda channels, native, folder, levels=None: (1, 10),
            encode_wide=outils.encode_wide, encode_signed=outils.encode_signed, format_time=outils.format_time)

    def sea_latest(self):
        now = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
        start = now - dt.timedelta(hours=now.hour % 6)
        return {"reference_time": f"{start:%Y-%m-%dT%H:%M}Z", "last_modified_time": "2026-11-21T09:17:02Z",
                "valid_times": [f"{start:%Y-%m-%dT%H:%M}Z"]}

    def test_sea_entry_keeps_its_old_fields_and_gains_the_new_ones(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mer, "read", lambda path, name: np.full(mer.SHAPE, 15.0, dtype=np.float32))
        latest = self.sea_latest()
        entry = mer.prepare_family(str(tmp_path), str(tmp_path), self.fake_tools(latest), "sea", limit=1,
                                   now=outils.parse_time(latest["reference_time"]) + dt.timedelta(hours=1))
        for name in ("path", "times", "levels", "model", "run", "source", "valid", "temperature"):
            assert name in entry, name
        assert entry["path"] == "m/{time}/{level}/{row}-{col}.png"
        assert entry["model"] == "meteofrance_sea_surface_temperature"
        assert entry["valid"] == {"channel": "blue", "from": 128, "open_sea": 255}
        assert TIME.match(entry["received"]) and entry["source_modified"] == "2026-11-21T09:17Z"
        assert entry["run"] == entry["times"][0]

    def test_current_entry_has_its_own_fields(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mer, "read", lambda path, name: np.full(mer.SHAPE, 0.5, dtype=np.float32))
        latest = self.sea_latest()
        entry = mer.prepare_family(str(tmp_path), str(tmp_path), self.fake_tools(latest), "current", limit=1,
                                   now=outils.parse_time(latest["reference_time"]) + dt.timedelta(hours=1))
        assert entry["path"] == "c/{time}/{level}/{row}-{col}.png"
        assert entry["current"] == {"east": "red", "north": "green", "kind": "sqrt", "max": 3.0, "unit": "m/s"}
        assert "temperature" not in entry

    def test_a_sea_family_that_fails_is_left(self, tmp_path):
        tools = self.fake_tools({})
        tools.fetch_json = lambda url: (_ for _ in ()).throw(OSError("serveur injoignable"))
        assert mer.prepare_family(str(tmp_path), str(tmp_path), tools, "sea") is None

    def test_fire_entry_gains_its_reception_only_when_given(self):
        days = [dt.date(2026, 11, 21)]
        assert "received" not in feu.entry(days)
        assert feu.entry(days, received="2026-11-21T09:30Z")["received"] == "2026-11-21T09:30Z"
        assert feu.entry(days, received="2026-11-21T09:30Z")["run"] == "2026-11-21T00:00Z"

    def test_an_old_fire_cache_gets_its_reception_from_its_build_time(self):
        meta = {"built": "2026-11-21T09:30:12Z", "entry": {"times": ["2026-11-21T12:00Z"]}}
        assert feu.kept_entry(meta)["received"] == "2026-11-21T09:30Z"
        assert "received" not in meta["entry"]  # le cache lui-même n'est pas modifié
        assert feu.kept_entry({"entry": {"received": "x"}, "built": "2026-11-21T09:30:12Z"})["received"] == "x"
        assert "received" not in feu.kept_entry({"entry": {"times": []}})
