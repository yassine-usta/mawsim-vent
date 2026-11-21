"""Aides communes aux essais pytest : un faux modèle ICON (sans réseau) et un petit site préparé une seule fois."""
import datetime as dt
import json
import os
import sys
import types

import numpy as np
import pytest

try:
    import omfiles  # noqa: F401
except ImportError:  # omfiles ne sert qu'à lire les vrais fichiers : un module vide suffit aux essais
    sys.modules["omfiles"] = types.SimpleNamespace(OmFileReader=None)

import outils  # noqa: E402
import prepare  # noqa: E402
import source_icon  # noqa: E402

RUN = "2026-11-21T06:00:00Z"
MODIFIED = "2026-11-21T09:17:02Z"
RECEIVED = "2026-11-21T09:25Z"
ROWS, COLS = 721, 1439  # la grille d'ICON : un quart de degré, une colonne de moins qu'un tour


def fake_latest(hours=3, completed=True):
    run = outils.parse_time(RUN)
    return {
        "completed": completed,
        "reference_time": RUN,
        "last_modified_time": MODIFIED,
        "valid_times": [f"{run + dt.timedelta(hours=h):%Y-%m-%dT%H:%MZ}" for h in range(hours)],
    }


class FakeVariable:
    def __init__(self, field):
        self.field = field

    def read_array(self, _slices):
        return self.field


class FakeReader:
    """Un fichier horaire d'ICON : les grandeurs demandées, ou ValueError comme omfiles pour une grandeur absente."""
    opened = 0

    def __init__(self, fields):
        self.fields = fields
        self.closed = False
        FakeReader.opened += 1

    def get_child_by_name(self, name):
        if name not in self.fields:
            raise ValueError(f"variable absente : {name}")
        return FakeVariable(self.fields[name])

    def close(self):
        self.closed = True


def smooth(low, high):
    """Une grille douce du sud au nord : se compresse vite et se code sans surprise."""
    return np.tile(np.linspace(low, high, ROWS, dtype=np.float32)[:, None], (1, COLS))


def icon_fields(gust=True):
    fields = {
        "wind_u_component_10m": smooth(-12.0, 12.0),
        "wind_v_component_10m": smooth(5.0, -5.0),
        "temperature_2m": smooth(-30.0, 35.0),
        "cloud_cover": smooth(0.0, 100.0),
        "pressure_msl": smooth(980.0, 1030.0),
        "precipitation": smooth(0.0, 6.0),
    }
    if gust:
        fields["wind_gusts_10m"] = smooth(2.0, 30.0)
    return fields


def install_fake_icon(monkeypatch, latest=None, fields=None):
    """Remplace le réseau et omfiles : latest.json, le téléchargement et la lecture des fichiers."""
    latest = latest or fake_latest()
    fields = fields or icon_fields()
    downloads = []

    def download(url, path, tries=4):
        downloads.append(url)
        with open(path, "wb") as out:
            out.write(b"om")

    monkeypatch.setattr(prepare, "fetch_json", lambda url, timeout=60: latest)
    monkeypatch.setattr(outils, "download", download)
    monkeypatch.setattr(source_icon, "open_reader", lambda path: FakeReader(fields))
    return downloads


@pytest.fixture(scope="module")
def small_site(tmp_path_factory):
    """Un site préparé avec le faux ICON : une heure, le vent, l'air et le ciel (sans mer ni feu)."""
    mp = pytest.MonkeyPatch()
    root = tmp_path_factory.mktemp("site")
    try:
        install_fake_icon(mp)
        site = str(root / "site")
        code = prepare.main([site, "--limit", "1", "--no-sea", "--no-fire", "--cache", str(root / "cache"), "--received", RECEIVED])
    finally:
        mp.undo()
    assert code == 0
    with open(os.path.join(site, "index.json"), encoding="utf-8") as source:
        return site, json.load(source)
