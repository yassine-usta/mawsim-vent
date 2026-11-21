"""Essais sans réseau des familles de CAMS (poussiere, uv) : codage et décodage, rééchantillonnage sur la maille de 0,5°,
heures, niveaux de tuiles, dates de l'index (celles de CAMS, pas celles d'ICON), famille laissée quand CAMS manque."""
import datetime as dt
import os

import numpy as np
import pytest
from PIL import Image

import outils
import prepare
import source_cams
from conftest import RECEIVED, FakeReader
from contexte import Contexte
from familles import poussiere, uv

UTC = dt.timezone.utc
CAMS_RUN = "2026-11-21T00:00:00Z"
CAMS_MODIFIED = "2026-11-21T10:27:30Z"


def at(text):
    return dt.datetime.fromisoformat(text).replace(tzinfo=UTC)


def cams_latest(completed=True, hours=121):
    run = outils.parse_time(CAMS_RUN)
    return {"completed": completed, "reference_time": CAMS_RUN, "last_modified_time": CAMS_MODIFIED,
            "valid_times": [f"{run + dt.timedelta(hours=h):%Y-%m-%dT%H:%MZ}" for h in range(hours)]}


def cams_fields():
    rows = np.linspace(0.0, 1.0, source_cams.ROWS, dtype=np.float32)[:, None]
    flat = np.ones((1, source_cams.COLS), dtype=np.float32)
    return {
        "dust": rows * flat * 3000.0,  # de 0 au pôle Sud à 3000 µg/m³ au pôle Nord
        "aerosol_optical_depth": rows * flat * 1.4,
        "uv_index": rows * flat * 12.0,
        "uv_index_clear_sky": rows * flat * 13.0,
    }


def install_fake_cams(monkeypatch, latest=None, fields=None, fail_fetch=False):
    latest = latest or cams_latest()
    fields = fields or cams_fields()
    downloads = []

    def fetch_json(url, timeout=60):
        if fail_fetch:
            raise OSError("pas de réseau")
        assert url.endswith("/cams_global/latest.json")
        return latest

    def download(url, path, tries=4):
        downloads.append(url)
        with open(path, "wb") as out:
            out.write(b"om")

    monkeypatch.setattr(outils, "download", download)
    monkeypatch.setattr(outils, "fetch_json", fetch_json)
    monkeypatch.setattr(source_cams, "open_reader", lambda path: FakeReader(fields))
    return downloads


def make_context(tmp_path, **changes):
    values = dict(out=str(tmp_path / "site"), work=str(tmp_path / "work"), run=outils.parse_time("2026-11-21T06:00:00Z"),
                  received=outils.parse_time(RECEIVED), latest={"completed": True, "reference_time": "2026-11-21T06:00:00Z"})
    values.update(changes)
    os.makedirs(values["out"], exist_ok=True)
    os.makedirs(values["work"], exist_ok=True)
    return Contexte(**values)


def tile(ctx, folder, hour, level, name):
    return np.asarray(Image.open(os.path.join(ctx.out, folder, hour, str(level), name)))


class TestPoussiere:
    def test_round_trip_keeps_small_and_huge_values_within_half_a_step(self):
        values = np.array([0.05, 0.3, 1.0, 3.0, 8.0, 25.0, 150.0, 800.0, 3425.0, 9000.0])
        back = poussiere.decode_dust(poussiere.encode_dust(values))
        assert poussiere.decode_dust(poussiere.encode_dust(np.array([0.0])))[0] == 0.0
        relative = np.expm1(np.log1p(poussiere.DUST_MAX / poussiere.DUST_REF) / 255.0)  # le pas : 3,7 % de (REF + valeur)
        for value, found in zip(values, back):
            assert abs(found - value) <= (poussiere.DUST_REF + value) * relative / 2 * 1.001, (value, found)

    def test_a_few_micrograms_and_a_storm_keep_distinct_bytes(self):
        octets = poussiere.encode_dust(np.array([1.0, 2.0, 3.0, 5.0, 3000.0, 3400.0]))
        assert list(octets) == sorted(set(octets))  # chacune a son octet
        assert octets[-1] < 255

    def test_the_maximum_and_beyond_are_clipped(self):
        assert list(poussiere.encode_dust(np.array([10000.0, 50000.0, -4.0]))) == [255, 255, 0]
        assert poussiere.decode_dust(255) == pytest.approx(10000.0)

    def test_channels(self):
        fields = {"dust": np.full((3, 4), 3.0, dtype=np.float32), "aerosol_optical_depth": np.full((3, 4), 0.2, dtype=np.float32)}
        red, green, blue = poussiere.channels(fields, 1)
        assert poussiere.decode_dust(red[0, 0]) == pytest.approx(3.0, rel=0.02)
        assert 5.0 * (green[0, 0] / 255.0) ** 2 == pytest.approx(0.2, abs=0.01)
        assert not blue.any()


class TestUv:
    def test_tenth_of_an_index_round_trip(self):
        values = np.arange(0.0, 25.6, 0.1)
        back = uv.encode_uv(values) / uv.SCALE
        assert np.max(np.abs(back - values)) <= 0.05 + 1e-9

    def test_clipping_and_channels(self):
        assert list(uv.encode_uv(np.array([-1.0, 30.0]))) == [0, 255]
        red, green, blue = uv.channels({"uv_index": np.full((2, 2), 7.34, dtype=np.float32),
                                        "uv_index_clear_sky": np.full((2, 2), 8.06, dtype=np.float32)}, 1)
        assert (red[0, 0], green[0, 0], blue[0, 0]) == (73, 81, 0)


class TestResample:
    def test_shape_is_the_half_degree_lattice_without_the_closing_column(self):
        assert source_cams.resample(cams_fields()["dust"]).shape == (361, 720)

    def test_a_constant_field_stays_constant(self):
        out = source_cams.resample(np.full((source_cams.ROWS, source_cams.COLS), 7.0, dtype=np.float32))
        assert np.allclose(out, 7.0)

    def test_nodes_that_coincide_keep_their_value_and_others_are_interpolated(self):
        field = np.random.default_rng(1).random((source_cams.ROWS, source_cams.COLS)).astype(np.float32)
        out = source_cams.resample(field)
        # 2° = 4 mailles de 0,5° = 5 mailles de 0,4° : le point tombe sur un point de la source
        assert out[4, 4] == pytest.approx(field[5, 5], abs=1e-6)
        # 0,5° est à 1,25 maille de la source : un quart du chemin entre la maille 1 et la maille 2
        assert out[0, 1] == pytest.approx(field[0, 1] * 0.75 + field[0, 2] * 0.25, abs=1e-6)

    def test_the_last_longitude_looks_at_the_first_column_to_close_the_circle(self):
        field = np.zeros((source_cams.ROWS, source_cams.COLS), dtype=np.float32)
        field[:, 0] = 10.0  # le méridien -180, voisin de 179,6
        out = source_cams.resample(field)
        # le dernier point, 179,5° = maille 898,75 : voisines 898 (0) et 899 (0) : pas de 10 ici
        assert out[0, -1] == pytest.approx(0.0, abs=1e-6)
        field[:, 899] = 8.0  # 179,6° : le point 179,5° en prend trois quarts et un quart de la maille 898 (0)
        assert source_cams.resample(field)[0, -1] == pytest.approx(8.0 * 0.75, abs=1e-6)

    def test_the_north_and_south_rows_are_kept(self):
        field = np.zeros((source_cams.ROWS, source_cams.COLS), dtype=np.float32)
        field[0], field[-1] = 1.0, 2.0
        out = source_cams.resample(field)
        assert np.allclose(out[0], 1.0) and np.allclose(out[-1], 2.0)


class TestHeures:
    def test_hourly_for_two_days_then_every_three_hours_from_the_vent_run(self):
        times = source_cams.wanted_times(cams_latest()["valid_times"], at("2026-11-21T06:00"), outils.parse_time)
        assert times[0] == at("2026-11-21T06:00")  # les heures d'avant le vent sont passées
        assert len([t for t in times if t <= at("2026-11-23T06:00")]) == 49
        assert all(t.hour % 3 == 0 for t in times if t > at("2026-11-23T06:00"))
        assert times[-1] == at("2026-11-26T00:00")  # la dernière échéance de CAMS (120 h)

    def test_the_dust_is_three_hourly_whatever_the_horizon_and_the_uv_follows_the_plan(self, tmp_path, monkeypatch):
        install_fake_cams(monkeypatch)
        ctx = make_context(tmp_path)
        cams = source_cams.plan(ctx)
        dust = source_cams.family_times(ctx, cams, poussiere)
        assert [t.hour for t in dust[:4]] == [6, 9, 12, 15]
        assert all(t.hour % 3 == 0 for t in dust)
        assert len(source_cams.family_times(ctx, cams, uv)) == len(cams.times)
        assert len(dust) < len(cams.times)

    def test_the_limit_applies_to_each_family(self, tmp_path, monkeypatch):
        install_fake_cams(monkeypatch)
        ctx = make_context(tmp_path, limit=2)
        cams = source_cams.plan(ctx)
        assert [t.hour for t in source_cams.family_times(ctx, cams, uv)] == [6, 7]
        assert [t.hour for t in source_cams.family_times(ctx, cams, poussiere)] == [6, 9]
        ctx.limit = -2
        assert len(source_cams.family_times(ctx, cams, uv)) == 2


class TestPassage:
    def test_both_families_write_two_levels_and_dated_entries(self, tmp_path, monkeypatch):
        downloads = install_fake_cams(monkeypatch)
        ctx = make_context(tmp_path, limit=2)
        entries = prepare.run_families(ctx, prepare.load_families(["poussiere", "uv"]))
        assert set(entries) == {"poussiere", "uv"}
        # heures 6, 7 (UV) et 6, 9 (poussière) : trois fichiers, chacun téléchargé une fois
        assert len(downloads) == 3 and downloads[0].endswith("/cams_global/2026/11/21/0000Z/2026-11-21T0600.om")
        assert entries["poussiere"]["times"] == ["2026-11-21T06:00Z", "2026-11-21T09:00Z"]
        assert entries["uv"]["times"] == ["2026-11-21T06:00Z", "2026-11-21T07:00Z"]
        for folder, name in (("d", "poussiere"), ("u", "uv")):
            entry = entries[name]
            assert entry["path"] == f"{folder}/{{time}}/{{level}}/{{row}}-{{col}}.png"
            assert [level["id"] for level in entry["levels"]] == [0, 1]
            assert [level["step"] for level in entry["levels"]] == [1.0, 0.5]
            assert not os.path.exists(os.path.join(ctx.out, folder, "2026112106", "2"))
            assert len(os.listdir(os.path.join(ctx.out, folder, "2026112106", "0"))) == 2
            assert len(os.listdir(os.path.join(ctx.out, folder, "2026112106", "1"))) == 8
            # les dates sont celles de CAMS, pas celles d'ICON (calcul de 6 h, reçu à 9 h 25)
            assert entry["run"] == "2026-11-21T00:00Z"
            assert entry["source_modified"] == "2026-11-21T10:27Z"
            assert entry["model"] == "cams_global"
        assert not os.path.exists(os.path.join(ctx.out, "d", "2026112107"))  # pas de poussière à 7 h
        # les fichiers de CAMS sont effacés quand toutes les familles qui les lisent les ont lus ; avec --limit 2, le
        # fichier de 9 h n'est lu que par la poussière (l'UV s'arrête à 7 h) : il reste
        assert os.listdir(ctx.work) == ["cams_global-2026112100-2026112109.om"]

    def test_pixels_decode_to_the_source_values(self, tmp_path, monkeypatch):
        install_fake_cams(monkeypatch)
        ctx = make_context(tmp_path, limit=1)
        prepare.run_families(ctx, prepare.load_families(["poussiere", "uv"]))
        image = tile(ctx, "d", "2026112106", 1, "0-0.png")
        assert image.shape == (181, 181, 3)
        assert image[-1, 10][0] == 0  # le pôle Sud, dans la dernière ligne de l'image : rien
        north = tile(ctx, "d", "2026112106", 1, "1-0.png")[0, 10]  # 90° nord : 3000 µg/m³
        assert poussiere.decode_dust(north[0]) == pytest.approx(3000.0, rel=0.02)
        assert 5.0 * (north[1] / 255.0) ** 2 == pytest.approx(1.4, abs=0.02)
        assert north[2] == 0
        assert tuple(tile(ctx, "u", "2026112106", 1, "1-0.png")[0, 10]) == (120, 130, 0)

    def test_neighbour_tiles_share_their_edge(self, tmp_path, monkeypatch):
        install_fake_cams(monkeypatch)
        ctx = make_context(tmp_path, limit=1)
        prepare.run_families(ctx, prepare.load_families(["poussiere"]))
        left = tile(ctx, "d", "2026112106", 1, "0-0.png")
        right = tile(ctx, "d", "2026112106", 1, "0-1.png")
        assert np.array_equal(left[:, -1], right[:, 0])

    def test_one_family_alone_works(self, tmp_path, monkeypatch):
        install_fake_cams(monkeypatch)
        ctx = make_context(tmp_path, limit=1)
        assert prepare.run_families(ctx, prepare.load_families(["uv"])).keys() == {"uv"}

    def test_an_incomplete_calculation_leaves_the_families(self, tmp_path, monkeypatch, capsys):
        install_fake_cams(monkeypatch, latest=cams_latest(completed=False))
        ctx = make_context(tmp_path)
        assert prepare.run_families(ctx, prepare.load_families(["poussiere", "uv"])) == {}
        assert "pas complet" in capsys.readouterr().out

    def test_an_unreachable_server_leaves_the_families_without_failing(self, tmp_path, monkeypatch):
        install_fake_cams(monkeypatch, fail_fetch=True)
        ctx = make_context(tmp_path)
        assert prepare.run_families(ctx, prepare.load_families(["poussiere", "uv"])) == {}

    def test_an_hour_with_a_missing_grid_is_left_and_the_others_stay(self, tmp_path, monkeypatch, capsys):
        fields = cams_fields()
        install_fake_cams(monkeypatch, fields=fields)
        opened = []

        def opener(path):
            opened.append(path)
            return FakeReader({k: v for k, v in fields.items() if k != "dust"} if len(opened) == 1 else fields)

        monkeypatch.setattr(source_cams, "open_reader", opener)
        ctx = make_context(tmp_path, limit=2)
        entries = prepare.run_families(ctx, prepare.load_families(["poussiere"]))
        assert entries["poussiere"]["times"] == ["2026-11-21T09:00Z"]
        assert "laissée" in capsys.readouterr().out

    def test_missing_values_leave_the_hour(self, tmp_path, monkeypatch):
        fields = cams_fields()
        fields["uv_index"][5, 5] = np.nan
        install_fake_cams(monkeypatch, fields=fields)
        ctx = make_context(tmp_path, limit=1)
        assert prepare.run_families(ctx, prepare.load_families(["uv"])) == {}
