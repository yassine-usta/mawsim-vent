"""Essais sans réseau de outils.download : jamais de fichier partiel dans le cache."""
import io
import os

import pytest

import outils


class FakeResponse(io.BytesIO):
    """Une réponse HTTP simulée : [length] est le Content-Length annoncé (None : pas d'en-tête)."""

    def __init__(self, data, length):
        super().__init__(data)
        self.headers = {} if length is None else {"Content-Length": str(length)}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def serve(monkeypatch, responses):
    """Chaque appel d'urlopen rend la réponse suivante (ou lève, si c'est une exception)."""
    queue = list(responses)
    calls = []

    def urlopen(url, timeout=None):
        calls.append(url)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(outils.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(outils.time, "sleep", lambda seconds: None)
    return calls


def test_complete_download_is_renamed_into_place(tmp_path, monkeypatch):
    serve(monkeypatch, [FakeResponse(b"0123456789", 10)])
    path = str(tmp_path / "a.om")
    outils.download("https://exemple/a.om", path)
    assert open(path, "rb").read() == b"0123456789"
    assert os.listdir(tmp_path) == ["a.om"]


def test_truncated_response_is_refused_and_retried(tmp_path, monkeypatch, capsys):
    calls = serve(monkeypatch, [FakeResponse(b"01234", 10), FakeResponse(b"0123456789", 10)])
    path = str(tmp_path / "a.om")
    outils.download("https://exemple/a.om", path)
    assert len(calls) == 2
    assert open(path, "rb").read() == b"0123456789"
    assert "5 octets reçus sur 10" in capsys.readouterr().out
    assert os.listdir(tmp_path) == ["a.om"]


def test_after_the_last_try_nothing_partial_is_left(tmp_path, monkeypatch):
    calls = serve(monkeypatch, [FakeResponse(b"012", 10) for _ in range(3)])
    path = str(tmp_path / "a.om")
    with pytest.raises(outils.TruncatedDownload, match="3 octets reçus sur 10"):
        outils.download("https://exemple/a.om", path, tries=3)
    assert len(calls) == 3
    assert os.listdir(tmp_path) == []  # ni a.om, ni a.om.part


def test_a_truncated_download_does_not_replace_a_good_file(tmp_path, monkeypatch):
    path = tmp_path / "a.om"
    path.write_bytes(b"ancien fichier complet")
    serve(monkeypatch, [FakeResponse(b"01", 10)])
    with pytest.raises(outils.TruncatedDownload):
        outils.download("https://exemple/a.om", str(path), tries=1)
    assert path.read_bytes() == b"ancien fichier complet"
    assert os.listdir(tmp_path) == ["a.om"]


def test_connection_error_is_retried_without_leaving_a_file(tmp_path, monkeypatch):
    calls = serve(monkeypatch, [ConnectionError("coupée"), FakeResponse(b"abc", 3)])
    outils.download("https://exemple/a.om", str(tmp_path / "a.om"))
    assert len(calls) == 2
    assert os.listdir(tmp_path) == ["a.om"]


def test_without_content_length_the_response_is_kept(tmp_path, monkeypatch):
    serve(monkeypatch, [FakeResponse(b"abc", None)])
    outils.download("https://exemple/a.om", str(tmp_path / "a.om"))
    assert (tmp_path / "a.om").read_bytes() == b"abc"
