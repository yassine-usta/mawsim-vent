"""Essais sans réseau de feu.py : python -m unittest test_feu"""
import datetime as dt
import io
import os
import sys
import tempfile
import types
import unittest

import numpy as np
from PIL import Image

try:
    import omfiles  # noqa: F401
except ImportError:  # omfiles ne sert qu'au vent et à la mer : un module vide suffit ici
    sys.modules["omfiles"] = types.SimpleNamespace(OmFileReader=None)

import feu  # noqa: E402
import mer  # noqa: E402
import prepare  # noqa: E402

WIDTH, HEIGHT = 512, 256  # une grille du serveur plus grossière que la vraie : mêmes règles, essais rapides
STEP = 360.0 / WIDTH


def centers():
    lat = 90.0 - (np.arange(HEIGHT) + 0.5) * STEP  # nord en haut, comme le TIFF
    lon = -180.0 + (np.arange(WIDTH) + 0.5) * STEP
    return np.meshgrid(lat, lon, indexing="ij")


def point(lat, lon):
    """Ligne et colonne d'un point des tuiles (1/12 de degré, ligne 0 au pôle Sud)."""
    return int(round((lat + 90) * 12)), int(round((lon + 180) * 12))


class Lattice(unittest.TestCase):
    def test_linear_field_is_reproduced(self):
        lat, lon = centers()
        value, inland = feu.lattice((300.0 + 2.0 * lat + 0.1 * lon).astype(np.float32))
        for la, lo in ((36.75, 9.25), (-25.0, 130.0), (0.0, 0.0), (60.5, -100.25)):
            row, col = point(la, lo)
            self.assertAlmostEqual(float(value[row, col]), 300.0 + 2.0 * la + 0.1 * lo, places=3)
            self.assertTrue(inland[row, col])
        self.assertEqual(value.shape, (2161, 4320))

    def test_rows_go_from_south_to_north(self):
        lat, _ = centers()
        value, _ = feu.lattice((100.0 + lat).astype(np.float32))
        self.assertLess(value[point(-60, 0)], value[point(60, 0)])

    def test_missing_cells_are_left_out(self):
        field = np.full((HEIGHT, WIDTH), 10.0, dtype=np.float32)
        lat, lon = centers()
        # Une maille vide parmi les quatre : la valeur vient des trois autres, et le point n'est plus en pleine terre.
        row, col = point(30.0, 20.0)
        y = (90.0 - 30.0) / STEP - 0.5
        x = (20.0 + 180.0) / STEP - 0.5
        j, i = int(np.floor(y)), int(np.floor(x))
        field[j, i] = np.nan
        field[j + 1, i + 1] = 40.0
        value, inland = feu.lattice(field)
        fy, fx = y - j, x - i
        weights = {(0, 1): (1 - fy) * fx, (1, 0): fy * (1 - fx), (1, 1): fy * fx}
        expected = (10.0 * (weights[0, 1] + weights[1, 0]) + 40.0 * weights[1, 1]) / sum(weights.values())
        self.assertAlmostEqual(float(value[row, col]), expected, places=4)
        self.assertFalse(inland[row, col])

    def test_no_value_around_gives_no_value(self):
        field = np.full((HEIGHT, WIDTH), np.nan, dtype=np.float32)
        field[100, 100] = 5.0
        value, inland = feu.lattice(field)
        far = point(0.0, 0.0)
        self.assertTrue(np.isnan(value[far]))
        self.assertFalse(inland.any())
        # Seuls les points entourés par cette maille ont une valeur, tous égaux à 5.
        self.assertTrue(np.allclose(value[~np.isnan(value)], 5.0))

    def test_date_line_wraps(self):
        field = np.full((HEIGHT, WIDTH), np.nan, dtype=np.float32)
        field[:, 0] = 2.0  # première maille à l'est de -180°
        field[:, -1] = 4.0  # dernière maille, à l'ouest de 180°
        value, inland = feu.lattice(field)
        # Le méridien -180° est à mi-chemin entre les deux.
        self.assertAlmostEqual(float(value[point(10.0, -180.0)]), 3.0, places=5)
        self.assertTrue(inland[point(10.0, -180.0)])

    def test_poles_use_the_last_row_only(self):
        field = np.full((HEIGHT, WIDTH), 7.0, dtype=np.float32)
        value, inland = feu.lattice(field)
        self.assertAlmostEqual(float(value[0, 100]), 7.0, places=5)
        self.assertAlmostEqual(float(value[-1, 100]), 7.0, places=5)
        self.assertFalse(inland[0, 100])
        self.assertFalse(inland[-1, 100])


class NoData(unittest.TestCase):
    def test_zero_and_bad_values_are_no_value(self):
        raw = np.array([[0.0, 1e-9, 12.5, -9999.0, np.inf, np.nan]], dtype=np.float32)
        out = feu.no_data(raw)
        self.assertTrue(np.isnan(out[0, 0]))
        self.assertAlmostEqual(float(out[0, 1]), 1e-9)
        self.assertEqual(float(out[0, 2]), 12.5)
        self.assertTrue(np.isnan(out[0, 3:]).all())

    def test_read_tiff_checks_size(self):
        image = Image.fromarray(np.ones((4, 8), dtype=np.float32), "F")
        buffer = io.BytesIO()
        image.save(buffer, "TIFF")
        self.assertEqual(feu.read_tiff(buffer.getvalue(), 8, 4).shape, (4, 8))
        with self.assertRaises(ValueError):
            feu.read_tiff(buffer.getvalue(), 16, 8)


class Extend(unittest.TestCase):
    def test_two_points_toward_the_sea(self):
        field = np.full((40, 60), np.nan, dtype=np.float32)
        field[:, :20] = 10.0  # la terre à l'ouest, la mer à l'est
        field[:, 10:20] = 20.0
        out = mer.extend(field, feu.SHORE)
        self.assertTrue(np.allclose(out[:, 20:22], 20.0))
        self.assertTrue(np.isnan(out[:, 22:58]).all())
        # La grille fait le tour du monde : la terre déborde aussi vers l'ouest, par l'autre bord.
        self.assertFalse(np.isnan(out[:, 58:]).any())
        self.assertTrue(np.array_equal(out[:, :20], field[:, :20]))


class Channels(unittest.TestCase):
    def test_two_bytes_and_blue(self):
        value = np.array([[12.34, 0.0, 655.35, 700.0, np.nan, 3.0]], dtype=np.float32)
        inland = np.array([[True, True, True, False, False, False]])
        red, green, blue = feu.fire_channels(value, inland, 1, prepare.encode_wide)
        index = (red.astype(int) * 256 + green) * 0.01
        self.assertAlmostEqual(float(index[0, 0]), 12.34, places=6)
        self.assertEqual(float(index[0, 1]), 0.0)
        self.assertAlmostEqual(float(index[0, 2]), 655.35, places=6)
        self.assertAlmostEqual(float(index[0, 3]), 655.35, places=6)  # plafond
        self.assertEqual((int(red[0, 4]), int(green[0, 4]), int(blue[0, 4])), (0, 0, 0))  # sans valeur
        self.assertEqual(blue[0].tolist(), [255, 255, 255, 128, 0, 128])

    def test_coarse_blue_counts_the_averaged_points(self):
        value = np.full((7, 12), np.nan, dtype=np.float32)
        inland = np.zeros((7, 12), dtype=bool)
        value[2:5, 2:5] = 10.0  # neuf points autour du point gardé (3, 3)
        inland[2:4, 2:5] = True  # six en pleine terre sur neuf
        _, _, blue = feu.fire_channels(value, inland, 3, prepare.encode_wide)
        self.assertEqual(int(blue[1, 1]), 255)
        inland[2:4, 2:5] = False
        inland[2, 2] = True
        _, _, blue = feu.fire_channels(value, inland, 3, prepare.encode_wide)
        self.assertEqual(int(blue[1, 1]), 128)


class Tiles(unittest.TestCase):
    def test_written_tile_reads_back(self):
        lat, lon = centers()
        raw = np.where(lat > -60.0, 20.0 + 0.5 * lat, 0.0).astype(np.float32)  # rien autour du pôle Sud
        with tempfile.TemporaryDirectory() as folder:
            count, _ = feu.write_day(raw, folder, prepare)
            self.assertEqual(count, 2 + 8 + 32 + 288)
            # Niveau 3, point 45° N, 10° E : tuile ligne 9 (135° à 150° de latitude depuis le sud), colonne 12.
            image = np.array(Image.open(os.path.join(folder, "3", "9-12.png")))
            self.assertEqual(image.shape, (181, 181, 3))
            row, col = point(45.0, 10.0)
            r, g, b = (int(v) for v in image[180 - (row - 9 * 180), col - 12 * 180])
            self.assertAlmostEqual((r * 256 + g) * 0.01, 20.0 + 0.5 * 45.0, places=2)
            self.assertEqual(b, 255)
            # Le pôle Sud : sans valeur, sauf la frange prolongée.
            image = np.array(Image.open(os.path.join(folder, "3", "0-0.png")))
            self.assertEqual(int(image[180, 0, 2]), 0)


class Index(unittest.TestCase):
    def test_entry_follows_the_contract(self):
        entry = feu.entry([dt.date(2026, 11, 18), dt.date(2026, 11, 19)])
        self.assertEqual(entry["path"], "f/{time}/{level}/{row}-{col}.png")
        self.assertEqual(entry["times"], ["2026-11-18T12:00Z", "2026-11-19T12:00Z"])
        self.assertEqual(entry["levels"], [
            {"id": 0, "step": 1.0, "span": 180.0, "rows": 1, "cols": 2},
            {"id": 1, "step": 0.5, "span": 90.0, "rows": 2, "cols": 4},
            {"id": 2, "step": 0.25, "span": 45.0, "rows": 4, "cols": 8},
            {"id": 3, "step": 0.08333333333333333, "span": 15.0, "rows": 12, "cols": 24},
        ])
        self.assertEqual(entry["index"], {"channels": "red,green", "low": 0.0, "quantum": 0.01, "unit": "FWI"})
        self.assertEqual(entry["valid"], {"channel": "blue", "from": 128, "inland": 255})
        self.assertEqual(entry["model"], "ecmwf.fwi")
        self.assertEqual(entry["run"], "2026-11-18T00:00Z")


class Cache(unittest.TestCase):
    meta = {"day": "2026-11-18", "entry": {"times": ["2026-11-18T12:00Z", "2026-11-19T12:00Z"]}}

    def test_other_day_is_stale(self):
        self.assertFalse(feu.still_fresh(self.meta, dt.date(2026, 11, 19), 10, check=lambda day: False))

    def test_new_day_on_the_server_is_stale(self):
        asked = []
        fresh = feu.still_fresh(self.meta, dt.date(2026, 11, 18), 10, check=lambda day: asked.append(day) or True)
        self.assertFalse(fresh)
        self.assertEqual(asked, [dt.date(2026, 11, 20)])

    def test_nothing_new_or_server_down_keeps_the_cache(self):
        self.assertTrue(feu.still_fresh(self.meta, dt.date(2026, 11, 18), 10, check=lambda day: False))
        self.assertTrue(feu.still_fresh(self.meta, dt.date(2026, 11, 18), 10, check=lambda day: None))

    def test_enough_days_needs_no_question(self):
        self.assertTrue(feu.still_fresh(self.meta, dt.date(2026, 11, 18), 2, check=lambda day: self.fail("question inutile")))

    def test_cache_is_reused_without_network(self):
        with tempfile.TemporaryDirectory() as root:
            cache, out, work = (os.path.join(root, name) for name in ("cache", "out", "work"))
            os.makedirs(os.path.join(cache, "f", "2026111812", "0"))
            os.makedirs(work)
            with open(os.path.join(cache, "f", "2026111812", "0", "0-0.png"), "wb") as tile:
                tile.write(b"png")
            with open(os.path.join(cache, feu.META), "w", encoding="utf-8") as meta:
                meta.write('{"day": "2026-11-18", "entry": {"times": ["2026-11-18T12:00Z"], "source": "x"}}')
            layers = feu.prepare(out, work, prepare, cache=cache, days=10, today=dt.date(2026, 11, 18), check=lambda day: False)
            self.assertEqual(layers, {"fire": {"times": ["2026-11-18T12:00Z"], "source": "x"}})
            self.assertTrue(os.path.exists(os.path.join(out, "f", "2026111812", "0", "0-0.png")))

    def test_failure_leaves_the_family_out(self):
        with tempfile.TemporaryDirectory() as root:
            missing = os.path.join(root, "absent.tif")
            layers = feu.prepare(os.path.join(root, "out"), root, prepare, days=1, today=dt.date(2026, 11, 18), tiff=missing)
            self.assertEqual(layers, {})


if __name__ == "__main__":
    unittest.main()
