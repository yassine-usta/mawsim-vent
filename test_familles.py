"""Essais sans réseau des familles : leur liste, leur interface, une famille de démonstration, le passage complet."""
import datetime as dt
import glob
import importlib
import os
import sys
import types

import numpy as np
import pytest

import outils
import prepare
import source_icon
from conftest import RECEIVED, FakeReader, fake_latest, icon_fields, install_fake_icon
from contexte import Contexte

HERE = os.path.dirname(os.path.abspath(__file__))


def make_context(tmp_path, **changes):
    values = dict(out=str(tmp_path / "site"), work=str(tmp_path / "work"), latest=fake_latest(),
                  run=outils.parse_time("2026-11-21T06:00:00Z"), received=outils.parse_time(RECEIVED))
    values.update(changes)
    os.makedirs(values["out"], exist_ok=True)
    os.makedirs(values["work"], exist_ok=True)
    return Contexte(**values)


class TestListe:
    def test_the_list_is_the_one_in_prepare(self):
        assert prepare.FAMILIES == ("vent", "air", "ciel", "nuages", "precipitations", "soleil", "poussiere", "uv", "eau", "courants", "feu")

    def test_every_module_of_the_folder_is_listed_and_every_name_has_a_module(self):
        files = {os.path.splitext(os.path.basename(path))[0] for path in glob.glob(os.path.join(HERE, "familles", "*.py"))}
        assert files - {"__init__"} == set(prepare.FAMILIES)

    def test_each_module_follows_the_interface(self):
        modules = prepare.load_families()
        assert [m.NAME for m in modules] == list(prepare.FAMILIES)
        for module in modules:
            assert module.__name__ == f"familles.{module.NAME}"
            assert callable(module.prepare)
            if hasattr(module, "write_hour"):  # famille horaire d'ICON
                assert callable(module.entry)
                assert module.FOLDER and isinstance(module.FOLDER, str)

    def test_places_in_the_index_are_distinct(self):
        layers = [m.LAYER for m in prepare.load_families()]
        assert len(set(layers)) == len(layers)
        assert layers.count(None) == 1  # seul le vent, la famille historique, est à la racine
        assert layers == [None, "air", "sky", "clouds", "precipitation", "sun", "dust", "uv", "sea", "current", "fire"]

    def test_hourly_families_are_those_that_read_the_icon_file(self):
        hourly = [m.NAME for m in prepare.load_families() if hasattr(m, "write_hour")]
        assert hourly == ["vent", "air", "ciel", "nuages", "precipitations", "soleil"]

    def test_a_subset_keeps_the_order_of_the_list(self):
        assert [m.NAME for m in prepare.load_families(["feu", "air", "vent"])] == ["vent", "air", "feu"]

    def test_unknown_family_is_refused(self):
        with pytest.raises(SystemExit, match="famille inconnue : nord"):
            prepare.load_families(["vent", "nord"])

    def test_options_remove_families(self):
        assert prepare.choose_families() == list(prepare.FAMILIES)
        assert prepare.choose_families(no_sea=True) == ["vent", "air", "ciel", "nuages", "precipitations", "soleil", "poussiere", "uv", "feu"]
        assert prepare.choose_families(no_fire=True) == ["vent", "air", "ciel", "nuages", "precipitations", "soleil", "poussiere", "uv", "eau", "courants"]
        assert prepare.choose_families("air, ciel", no_sea=True) == ["air", "ciel"]
        assert prepare.choose_families("eau,feu", no_sea=True) == ["feu"]

    def test_a_module_without_prepare_is_refused(self):
        bad = types.ModuleType("familles.bad")
        bad.NAME, bad.LAYER = "bad", "bad"
        with pytest.raises(SystemExit, match="prepare"):
            prepare.check_families([bad])

    def test_two_families_cannot_share_their_place(self):
        a, b = types.ModuleType("familles.a"), types.ModuleType("familles.b")
        for module, name in ((a, "a"), (b, "b")):
            module.NAME, module.LAYER, module.prepare = name, "same", lambda ctx: None
        with pytest.raises(SystemExit, match="déjà pris"):
            prepare.check_families([a, b])


class TestFamilleDeDemonstration:
    """Une famille ajoutée par ce seul test : un module et un nom dans la liste, aucun autre fichier touché."""

    @pytest.fixture
    def demo(self, monkeypatch):
        calls = []
        module = types.ModuleType("familles.demo")
        module.NAME, module.LAYER = "demo", "demo"

        def prepare_demo(ctx):
            calls.append(ctx.out)
            return {"path": "d/{time}/{level}/{row}-{col}.png", "times": ["2026-11-21T06:00Z"], **ctx.provenance(model="demo")}

        module.prepare = prepare_demo
        monkeypatch.setitem(sys.modules, "familles.demo", module)
        monkeypatch.setattr(prepare, "FAMILIES", prepare.FAMILIES + ("demo",))
        return calls

    def test_it_is_loaded_in_the_list(self, demo):
        assert [m.NAME for m in prepare.load_families()][-1] == "demo"
        assert prepare.choose_families(no_sea=True, no_fire=True) == ["vent", "air", "ciel", "nuages", "precipitations", "soleil", "poussiere", "uv", "demo"]

    def test_its_entry_goes_to_the_index(self, demo, tmp_path):
        ctx = make_context(tmp_path)
        modules = prepare.load_families(["demo"])
        entries = prepare.run_families(ctx, modules)
        index = prepare.build_index(ctx, modules, entries)
        assert demo == [ctx.out]
        assert index["layers"]["demo"]["run"] == "2026-11-21T06:00Z"
        assert index["layers"]["demo"]["received"] == RECEIVED
        assert index["layers"]["demo"]["model"] == "demo"

    def test_a_failing_family_is_left_and_the_others_stay(self, demo, tmp_path, monkeypatch, capsys):
        broken = types.ModuleType("familles.broken")
        broken.NAME, broken.LAYER = "broken", "broken"
        broken.prepare = lambda ctx: 1 / 0
        monkeypatch.setitem(sys.modules, "familles.broken", broken)
        monkeypatch.setattr(prepare, "FAMILIES", prepare.FAMILIES + ("broken",))
        ctx = make_context(tmp_path)
        entries = prepare.run_families(ctx, prepare.load_families(["broken", "demo"]))
        assert list(entries) == ["demo"]
        assert "broken : famille laissée" in capsys.readouterr().out

    def test_hourly_families_share_the_download(self, tmp_path, monkeypatch):
        seen = []

        def hourly(name, layer, variable):
            module = types.ModuleType(f"familles.{name}")
            module.NAME, module.LAYER, module.FOLDER = name, layer, name[0]
            module.write_hour = lambda ctx, frame: (seen.append((name, frame.valid.hour, frame.raw(variable).shape)) or (1, 100))
            module.entry = lambda ctx, times: {"times": [outils.format_time(t) for t in times]}
            module.prepare = lambda ctx: source_icon.prepare_alone(module, ctx)
            monkeypatch.setitem(sys.modules, f"familles.{name}", module)

        hourly("neige", "snow", "snowfall")
        hourly("grele", "hail", "hail")
        monkeypatch.setattr(prepare, "FAMILIES", ("neige", "grele"))
        fields = icon_fields()
        fields["snowfall"] = np.ones((3, 3), dtype=np.float32)
        fields["hail"] = np.ones((4, 4), dtype=np.float32)
        downloads = install_fake_icon(monkeypatch, fields=fields)
        ctx = make_context(tmp_path, limit=2)
        entries = prepare.run_families(ctx, prepare.load_families())
        assert len(downloads) == 2  # deux heures, un seul téléchargement par heure pour les deux familles
        assert seen == [("neige", 6, (3, 3)), ("grele", 6, (4, 4)), ("neige", 7, (3, 3)), ("grele", 7, (4, 4))]
        assert entries["neige"]["times"] == ["2026-11-21T06:00Z", "2026-11-21T07:00Z"]
        assert entries["grele"]["times"] == entries["neige"]["times"]


class TestSourceIcon:
    def test_plan_is_computed_once_and_follows_the_limit(self, tmp_path):
        ctx = make_context(tmp_path, latest=fake_latest(hours=10), limit=3)
        plan = source_icon.plan(ctx)
        assert [valid.hour for valid, _ in plan.times] == [6, 7, 8]
        assert source_icon.plan(ctx) is plan
        assert plan.older == []

    def test_negative_limit_adds_one_hour_of_the_previous_run(self, tmp_path):
        ctx = make_context(tmp_path, latest=fake_latest(hours=10), limit=-2)
        plan = source_icon.plan(ctx)
        assert len(plan.older) == 1
        assert plan.times[-1][1] == plan.previous == outils.parse_time("2026-11-21T00:00:00Z")

    def test_wanted_times_hourly_then_every_three_hours(self):
        run = outils.parse_time("2026-11-21T06:00:00Z")
        every = [f"{run + dt.timedelta(hours=h):%Y-%m-%dT%H:%MZ}" for h in range(0, 200)]
        hours = [round((t - run).total_seconds() / 3600) for t in source_icon.wanted_times(run, every, outils.parse_time)]
        assert hours[:49] == list(range(49))
        assert hours[49:52] == [51, 54, 57]
        assert hours[-1] == 180

    def test_a_missing_variable_is_none_not_an_error(self):
        frame = source_icon.Frame(FakeReader({"a": np.zeros((2, 2), dtype=np.float32)}), None, None, "m")
        assert frame.raw("a").shape == (2, 2)
        assert frame.raw("absente") is None

    def test_the_file_is_deleted_without_cache_and_the_reader_closed(self, tmp_path, monkeypatch):
        install_fake_icon(monkeypatch)
        opened = []
        monkeypatch.setattr(source_icon, "open_reader", lambda path: opened.append(FakeReader(icon_fields())) or opened[-1])
        ctx = make_context(tmp_path, limit=1, keep_files=False)
        vent = prepare.load_families(["air"])[0]
        source_icon.run_hours(ctx, [vent])
        assert os.listdir(ctx.work) == []
        assert opened[0].closed

    def test_without_gusts_the_mean_wind_stands_in(self, tmp_path, monkeypatch):
        install_fake_icon(monkeypatch, fields=icon_fields(gust=False))
        ctx = make_context(tmp_path, limit=1)
        vent = prepare.load_families(["vent"])[0]
        done = source_icon.run_hours(ctx, [vent])
        assert len(done["vent"]) == 1
        assert os.path.isfile(os.path.join(ctx.out, "t", "2026112106", "0", "0-0.png"))

    def test_an_hour_with_missing_wind_is_set_aside_but_not_the_air(self, tmp_path, monkeypatch, capsys):
        fields = icon_fields()
        fields["wind_u_component_10m"] = fields["wind_u_component_10m"].copy()
        fields["wind_u_component_10m"][10, 10] = np.nan
        install_fake_icon(monkeypatch, fields=fields)
        ctx = make_context(tmp_path, limit=1)
        modules = prepare.load_families(["vent", "air"])
        with pytest.raises(SystemExit, match="Moins de la moitié"):
            source_icon.run_hours(ctx, modules)
        assert "valeurs manquantes, heure écartée" in capsys.readouterr().out
        # L'air de cette heure a été préparé : une famille n'écarte pas les heures d'une autre.
        assert os.path.isfile(os.path.join(ctx.out, "a", "2026112106", "0", "0-0.png"))
        assert not os.path.exists(os.path.join(ctx.out, "t"))
