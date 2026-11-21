"""Essais sans réseau des familles nuages, precipitations et soleil : codage et décodage, ensoleillement sur des cas
connus et contre des valeurs relevées sur l'API d'Open-Meteo, inscription dans la liste et dans l'index."""
import datetime as dt
import json
import math
import os

import numpy as np
import pytest
from PIL import Image

import comparer
import outils
import prepare
import source_icon
from conftest import COLS, MODIFIED, RECEIVED, ROWS, fake_latest, icon_fields, install_fake_icon, FakeReader
from familles import nuages, precipitations, soleil

UTC = dt.timezone.utc


def at(text):
    return dt.datetime.fromisoformat(text).replace(tzinfo=UTC)


def decode_sqrt(byte, top):
    return top * (np.asarray(byte, dtype=np.float64) / 255.0) ** 2


# ---------------------------------------------------------------------------------------------------------------------
# Nuages


class TestNuages:
    def test_round_trip_within_half_a_step(self):
        values = np.linspace(0.0, 100.0, 1001, dtype=np.float32)[None, :]
        for channel in nuages.cloud_channels(values, values, values, 1):
            assert channel.dtype == np.uint8
            assert np.max(np.abs(channel / nuages.SCALE - values)) <= 0.2 + 1e-4

    def test_each_level_has_its_channel_and_values_are_clipped(self):
        low, mid, high = (np.full((3, 4), v, dtype=np.float32) for v in (10.0, 50.0, 130.0))
        red, green, blue = nuages.cloud_channels(low, mid, high, 1)
        assert (red == 25).all() and (green == 125).all() and (blue == 250).all()
        red, _, _ = nuages.cloud_channels(np.full((3, 4), -5.0, dtype=np.float32), mid, high, 1)
        assert (red == 0).all()


# ---------------------------------------------------------------------------------------------------------------------
# Précipitations


class TestPrecipitations:
    def test_rain_snow_and_graupel_make_the_total(self):
        total = np.array([3.6, 4.1, 0.1, 1.8, 0.7], dtype=np.float32)
        rain = np.array([1.0, 1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        showers = np.array([0.5, 0.5, 0.0, 0.0, 0.1], dtype=np.float32)
        snow = np.array([2.0, 2.0, 0.0, 0.0, 0.6], dtype=np.float32)
        liquid, solid, rest = precipitations.split(total, rain, showers, snow)
        assert np.allclose(liquid, [1.5, 1.5, 0.0, 0.0, 0.1])
        assert np.allclose(solid, [2.0, 2.0, 0.0, 0.0, 0.6])
        # 0,1 mm de reste n'est qu'un arrondi (point 1 et 3) ; 0,6 et 1,8 mm sont de la neige roulée.
        assert np.allclose(rest, [0.0, 0.6, 0.0, 1.8, 0.0])
        assert np.all(np.abs(liquid + solid + rest - total) <= 0.2 + 1e-6)

    def test_rates_round_trip_by_the_square_root(self):
        values = np.array([0.0, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0], dtype=np.float32)
        for channel in precipitations.precipitation_channels(values[None, :], values[None, :], values[None, :], 1):
            back = decode_sqrt(channel[0], precipitations.RATE_MAX)
            # Un octet de racine : un demi-pas vaut racine(60 x valeur) / 255, soit 0,03 mm/h vers 1 mm/h, 0,24 à 60 mm/h.
            assert np.all(np.abs(back - values) <= np.sqrt(60.0 * values) / 255 + 60.0 / 255 ** 2)

    def test_the_step_is_one_hour_then_three(self):
        ctx = type("Ctx", (), {"model": "dwd_icon"})()
        run = at("2026-11-21T06:00")
        frame = lambda hours: source_icon.Frame(None, run + dt.timedelta(hours=hours), run, "dwd_icon")
        assert precipitations.span_hours(ctx, frame(1)) == 1.0
        assert precipitations.span_hours(ctx, frame(78)) == 1.0
        assert precipitations.span_hours(ctx, frame(81)) == 3.0
        assert soleil.span_hours(ctx, frame(51)) == 1.0
        assert soleil.span_hours(ctx, frame(180)) == 3.0


# ---------------------------------------------------------------------------------------------------------------------
# Soleil


def solar_noon_longitude(moment):
    """La longitude où le soleil passe au méridien à [moment]."""
    declination, equation = soleil.sun_position(moment)
    hour = moment.hour + moment.minute / 60.0
    return declination, -15.0 * (hour - 12.0 + equation)


def elevation_sine(latitude, longitude, moment):
    """Sinus de la hauteur du soleil, calculé à part (trigonométrie sphérique ordinaire)."""
    declination, equation = soleil.sun_position(moment)
    hour = (moment - moment.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds() / 3600.0
    angle = math.radians(15.0 * (hour - 12.0 + equation) + longitude)
    lat, dec = math.radians(latitude), math.radians(declination)
    return math.sin(lat) * math.sin(dec) + math.cos(lat) * math.cos(dec) * math.cos(angle)


def brute_force(direct, latitude, longitude, end, seconds, samples=3600):
    """L'ensoleillement par la définition, échantillonnée seconde par seconde : hauteur moyenne du soleil sur la partie
    du pas où il est levé, DNI, puis la rampe d'Open-Meteo de 60 à 180 W/m²."""
    sines = [elevation_sine(latitude, longitude, end - dt.timedelta(seconds=seconds * (k + 0.5) / samples)) for k in range(samples)]
    up = [s for s in sines if s > 0]
    if not up or direct <= 0:
        return 0.0
    daylight = seconds * len(up) / samples
    dni = direct / (sum(up) / len(up))
    return min(max(dni - 60.0, 0.0) / 120.0 * daylight, daylight)


# Valeurs relevées le 21 novembre 2026 sur l'API d'Open-Meteo (models=icon_global, cell_selection=nearest) : maille,
# fin de l'heure (UTC), direct_radiation (W/m², moyenne de l'heure écoulée) et sunshine_duration (secondes).
OPEN_METEO = [
    (36.75, 10.125, "2026-11-21T07:00", 19.0, 3114.61),
    (-32.0, 115.875, "2026-11-21T11:00", 8.0, 685.8),
    (28.625, 77.25, "2026-11-24T12:00", 13.0, 2266.05),
    (38.375, 132.0, "2026-11-25T03:00", 43.0, 750.24),
    (48.875, 2.375, "2026-11-21T16:00", 6.0, 823.18),
    (49.375, 157.375, "2026-11-22T22:00", 7.0, 1130.04),
    (49.375, 157.375, "2026-11-23T05:00", 21.0, 2653.75),
    (49.375, 157.375, "2026-11-24T05:00", 16.0, 1654.8),
    (51.875, -93.75, "2026-11-24T15:00", 8.0, 1611.93),
    (61.5, -59.25, "2026-11-25T15:00", 16.0, 2582.9),
]


class TestPositionDuSoleil:
    @pytest.mark.parametrize("moment, declination, equation_minutes", [
        ("2026-06-21T12:00", 23.43, -1.7),
        ("2026-12-21T12:00", -23.43, 1.9),
        ("2026-11-03T12:00", -15.2, 16.4),
        ("2026-02-11T12:00", -14.0, -14.2),
        ("2026-03-20T15:00", 0.0, -7.5),
    ])
    def test_known_dates(self, moment, declination, equation_minutes):
        found, equation = soleil.sun_position(at(moment))
        assert found == pytest.approx(declination, abs=0.15)
        assert equation * 60 == pytest.approx(equation_minutes, abs=0.5)


class TestEnsoleillement:
    def test_night_gives_nothing_even_with_radiation(self):
        assert soleil.sunshine_seconds(300.0, 0.0, 0.0, at("2026-11-21T00:00"), 3600.0) == 0.0
        assert soleil.sunshine_seconds(300.0, 80.0, 20.0, at("2026-12-21T12:00"), 3600.0) == 0.0  # nuit polaire

    def test_no_radiation_gives_nothing(self):
        latitude, longitude = solar_noon_longitude(at("2026-11-21T12:00"))
        assert soleil.sunshine_seconds(0.0, latitude, longitude, at("2026-11-21T12:30"), 3600.0) == 0.0

    def test_sun_at_the_zenith(self):
        end = at("2026-11-21T12:30")
        latitude, longitude = solar_noon_longitude(at("2026-11-21T12:00"))
        # Soleil au zénith au milieu de l'heure : le DNI vaut à peu près le rayonnement direct horizontal.
        assert soleil.sunshine_seconds(500.0, latitude, longitude, end, 3600.0) == pytest.approx(3600.0)
        assert soleil.sunshine_seconds(180.0, latitude, longitude, end, 3600.0) == pytest.approx(3600.0)
        assert soleil.sunshine_seconds(120.0, latitude, longitude, end, 3600.0) == pytest.approx(1810.0, abs=10.0)
        assert soleil.sunshine_seconds(100.0, latitude, longitude, end, 3600.0) == pytest.approx(1210.0, abs=10.0)
        assert soleil.sunshine_seconds(59.0, latitude, longitude, end, 3600.0) == 0.0

    def test_grazing_sun_matches_the_definition(self):
        # Tunis, 21 novembre : le soleil se lève vers 6 h 10 UTC ; de 7 h à 8 h il monte d'environ 7 à 18 degrés.
        latitude, longitude, end = 36.8, 10.2, at("2026-11-21T08:00")
        assert 0.1 < elevation_sine(latitude, longitude, end - dt.timedelta(hours=1)) < elevation_sine(latitude, longitude, end) < 0.35
        for direct in (5.0, 10.0, 20.0, 40.0, 200.0):
            ours = float(soleil.sunshine_seconds(direct, latitude, longitude, end, 3600.0))
            assert ours == pytest.approx(brute_force(direct, latitude, longitude, end, 3600.0), abs=15.0)

    def test_sunrise_inside_the_hour_bounds_the_sunshine(self):
        # Le soleil se lève pendant l'heure : jamais plus de soleil que de jour, même avec un fort rayonnement.
        latitude, longitude, end = 36.8, 10.2, at("2026-11-21T07:00")
        mean_sine, daylight, night = soleil.daylight_geometry(latitude, longitude, end, 3600.0)
        assert not night and 0.0 < daylight < 3600.0
        expected_daylight = brute_force(10_000.0, latitude, longitude, end, 3600.0)  # toute la partie levée
        assert daylight == pytest.approx(expected_daylight, abs=20.0)
        assert soleil.sunshine_seconds(500.0, latitude, longitude, end, 3600.0) == pytest.approx(daylight)

    @pytest.mark.parametrize("latitude, longitude, end, direct, expected", OPEN_METEO)
    def test_same_as_open_meteo(self, latitude, longitude, end, direct, expected):
        ours = float(soleil.sunshine_seconds(direct, latitude, longitude, at(end), 3600.0))
        assert ours == pytest.approx(expected, abs=20.0)

    def test_on_a_grid_like_point_by_point(self):
        end = at("2026-11-21T07:00")
        latitude = np.array([[-60.0], [0.0], [36.75], [60.0]])
        longitude = np.array([[-120.0, 0.0, 10.125, 60.0, 179.875]])
        direct = np.full((4, 5), 30.0)
        grid = soleil.sunshine_seconds(direct, latitude, longitude, end, 3600.0)
        for i in range(4):
            for j in range(5):
                assert grid[i, j] == soleil.sunshine_seconds(30.0, latitude[i, 0], longitude[0, j], end, 3600.0)

    def test_three_hour_step_is_spread_over_its_hours(self):
        # Trois heures en plein midi, ciel clair : 60 minutes par heure ; trois heures de nuit : rien.
        latitude, longitude = solar_noon_longitude(at("2026-11-21T12:00"))
        assert soleil.sunshine_per_hour(600.0, latitude, longitude, at("2026-11-21T13:30"), 3) == pytest.approx(60.0)
        assert soleil.sunshine_per_hour(600.0, latitude, longitude + 180.0, at("2026-11-21T13:30"), 3) == 0.0
        # Un pas d'une heure : la même chose que sunshine_seconds.
        end = at("2026-11-21T08:00")
        one = soleil.sunshine_per_hour(20.0, 36.8, 10.2, end, 1)
        assert one == pytest.approx(soleil.sunshine_seconds(20.0, 36.8, 10.2, end, 3600.0) / 60.0)

    def test_three_hour_step_at_sunrise_counts_hour_by_hour(self):
        # Pas de 6 h à 9 h UTC à Tunis, le soleil se lève vers 6 h 10 : avec la clarté tenue constante, chaque heure
        # reçoit sa part du rayonnement du pas, puis son ensoleillement est compté comme Open-Meteo le compte.
        latitude, longitude, end = 36.8, 10.2, at("2026-11-21T09:00")
        direct = 120.0
        shares = [soleil.top_of_atmosphere(latitude, longitude, end - dt.timedelta(hours=2 - k), 3600.0) for k in range(3)]
        hours = [direct * s / (sum(shares) / 3) for s in shares]
        expected = sum(soleil.sunshine_seconds(h, latitude, longitude, end - dt.timedelta(hours=2 - k), 3600.0)
                       for k, h in enumerate(hours)) / 3 / 60
        assert soleil.sunshine_per_hour(direct, latitude, longitude, end, 3) == pytest.approx(expected)
        assert shares[0] < shares[1] < shares[2]


class TestCodageDuSoleil:
    def test_round_trip(self):
        radiation = np.array([[0.0, 2.4, 2.6, 412.0, 1000.0, 1275.0, 1400.0]], dtype=np.float32)
        minutes = np.array([[0.0, 0.1, 0.2, 30.0, 59.9, 60.0, 61.0]], dtype=np.float32)
        red, green, blue = soleil.sun_channels(radiation, minutes, 1)
        assert red.tolist()[0] == [0, 0, 1, 82, 200, 255, 255]
        assert green.tolist()[0] == [0, 0, 1, 120, 240, 240, 240]
        assert not blue.any()
        back = red * soleil.RADIATION_QUANTUM
        assert np.all(np.abs(back[0, :6] - radiation[0, :6]) <= 2.5)
        assert np.all(np.abs(green / soleil.MINUTES_SCALE - minutes)[0, :6] <= 0.125 + 1e-6)


# ---------------------------------------------------------------------------------------------------------------------
# Inscription et passage complet


def a1_fields():
    """Le faux ICON, avec les grandeurs des nouvelles familles, constantes (faciles à relire dans une tuile)."""
    fields = icon_fields()
    constant = lambda value: np.full((ROWS, COLS), value, dtype=np.float32)
    fields.update({
        "cloud_cover_low": constant(20.0), "cloud_cover_mid": constant(60.0), "cloud_cover_high": constant(100.0),
        "precipitation": constant(4.0), "rain": constant(1.0), "showers": constant(0.5),
        "snowfall_water_equivalent": constant(2.0),
        "direct_radiation": constant(400.0), "diffuse_radiation": constant(100.0),
    })
    return fields


def install_a1_icon(monkeypatch):
    """Le faux ICON, sur trois heures : l'heure du calcul n'a ni précipitations ni rayonnement, comme le vrai."""
    install_fake_icon(monkeypatch, latest=fake_latest(hours=3), fields=a1_fields())
    first = {name: value for name, value in a1_fields().items() if name not in (
        "precipitation", "rain", "showers", "snowfall_water_equivalent", "direct_radiation", "diffuse_radiation")}
    monkeypatch.setattr(source_icon, "open_reader",
                        lambda path: FakeReader(first if path.endswith("2026112106.om") else a1_fields()))


def pixel(site, folder, stamp, level=0, row=0, col=0):
    with Image.open(os.path.join(site, folder, stamp, str(level), f"{row}-{col}.png")) as image:
        assert image.mode == "RGB" and image.size == (181, 181)
        return image.getpixel((90, 90))


class TestInscription:
    def test_the_families_are_listed_and_hourly(self):
        modules = {m.NAME: m for m in prepare.load_families()}
        for name, layer, folder in (("nuages", "clouds", "n"), ("precipitations", "precipitation", "p"), ("soleil", "sun", "r")):
            assert name in prepare.FAMILIES
            assert modules[name].LAYER == layer and modules[name].FOLDER == folder
            assert hasattr(modules[name], "write_hour")
        folders = [m.FOLDER for m in modules.values() if hasattr(m, "FOLDER")]
        assert len(set(folders)) == len(folders)
        assert not {"n", "p", "r"} & {"t", "a", "s", "m", "c", "f"}

    @pytest.fixture
    def site(self, tmp_path, monkeypatch):
        install_a1_icon(monkeypatch)
        out = tmp_path / "site"
        code = prepare.main([str(out), "--limit", "2", "--familles", "vent,nuages,precipitations,soleil",
                             "--cache", str(tmp_path / "c"), "--received", RECEIVED])
        assert code == 0
        with open(out / "index.json", encoding="utf-8") as source:
            return str(out), json.load(source)

    def test_index_entries(self, site):
        _, index = site
        assert list(index["layers"]) == ["clouds", "precipitation", "sun"]
        clouds, rain, sun = (index["layers"][key] for key in ("clouds", "precipitation", "sun"))
        assert clouds["path"] == "n/{time}/{level}/{row}-{col}.png"
        assert rain["path"] == "p/{time}/{level}/{row}-{col}.png"
        assert sun["path"] == "r/{time}/{level}/{row}-{col}.png"
        assert clouds["times"] == ["2026-11-21T06:00Z", "2026-11-21T07:00Z"]
        # L'heure du calcul n'a ni précipitations ni rayonnement : elle manque à ces deux familles.
        assert rain["times"] == sun["times"] == ["2026-11-21T07:00Z"]
        for entry in (clouds, rain, sun):
            assert entry["model"] == "dwd_icon"
            assert entry["run"] == "2026-11-21T06:00Z"
            assert entry["received"] == RECEIVED
            assert entry["source_modified"] == outils.format_time(outils.parse_time(MODIFIED))
        assert clouds["low"] == {"channel": "red", "scale": 2.5, "unit": "%"}
        assert rain["snow"]["channel"] == "green" and rain["snow"]["max"] == 60.0
        assert sun["radiation"] == {"channel": "red", "quantum": 5.0, "unit": "W/m²"}
        assert sun["sunshine"] == {"channel": "green", "scale": 4.0, "unit": "min/h"}

    def test_tiles_decode_to_the_values(self, site):
        out, _ = site
        for level, count in ((0, 2), (1, 8), (2, 32)):
            assert len(os.listdir(os.path.join(out, "n", "2026112107", str(level)))) == count
        assert pixel(out, "n", "2026112107", 2, 1, 3) == (50, 150, 250)
        red, green, blue = pixel(out, "p", "2026112107", 1, 1, 2)
        assert decode_sqrt(red, 60.0) == pytest.approx(1.5, abs=0.04)  # pluie et averses (demi-pas : 0,038)
        assert decode_sqrt(green, 60.0) == pytest.approx(2.0, abs=0.05)  # neige
        assert decode_sqrt(blue, 60.0) == pytest.approx(0.5, abs=0.03)  # neige roulée : 4 - 1,5 - 2
        red, green, blue = pixel(out, "r", "2026112107", 0, 0, 0)
        assert red * 5 == 500 and blue == 0
        assert 0 <= green <= 240
        assert not os.path.exists(os.path.join(out, "p", "2026112106"))

    def test_old_families_are_unchanged_byte_for_byte(self, tmp_path, monkeypatch):
        install_a1_icon(monkeypatch)
        before, after = tmp_path / "avant", tmp_path / "apres"
        common = ["--limit", "2", "--cache", str(tmp_path / "c"), "--received", RECEIVED, "--no-sea", "--no-fire"]
        assert prepare.main([str(before), "--familles", "vent,air,ciel", *common]) == 0
        assert prepare.main([str(after), *common]) == 0
        problems, added, same = comparer.compare(str(before), str(after))
        assert same == 2 * 3 * 42
        assert all(p.startswith("fichier en trop dans le nouveau : ") and p.split(" : ")[1][0] in "npr" for p in problems)
        assert sorted(added) == ["layers.clouds", "layers.precipitation", "layers.sun"]
