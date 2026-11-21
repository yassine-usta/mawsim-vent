"""Essais de comparer.py, qui prouve que deux sites préparés sont les mêmes."""
import json
import os

import comparer


def make_site(root, index, tiles):
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "index.json"), "w", encoding="utf-8") as out:
        json.dump(index, out)
    for name, data in tiles.items():
        path = os.path.join(root, *name.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as out:
            out.write(data)
    return str(root)


INDEX = {"version": 1, "run": "r", "created": "A", "layers": {"air": {"times": ["x"], "unit": "C"}}}
TILES = {"t/1/0/0-0.png": b"a", "a/1/0/0-0.png": b"b"}


def test_same_tiles_and_old_fields_with_new_fields_added(tmp_path):
    new_index = json.loads(json.dumps(INDEX))
    new_index["created"] = "B"
    new_index["received"] = "R"
    new_index["layers"]["air"]["run"] = "r"
    problems, added, same = comparer.compare(make_site(tmp_path / "old", INDEX, TILES), make_site(tmp_path / "new", new_index, TILES))
    assert problems == []
    assert sorted(added) == ["layers.air.run", "received"]
    assert same == 2


def test_a_changed_tile_is_found(tmp_path):
    problems, _, same = comparer.compare(make_site(tmp_path / "old", INDEX, TILES),
                                         make_site(tmp_path / "new", INDEX, {**TILES, "a/1/0/0-0.png": b"autre"}))
    assert problems == ["octets différents : a/1/0/0-0.png"] and same == 1


def test_a_missing_or_extra_tile_is_found(tmp_path):
    problems, _, _ = comparer.compare(make_site(tmp_path / "old", INDEX, TILES),
                                      make_site(tmp_path / "new", INDEX, {"t/1/0/0-0.png": b"a", "s/1/0/0-0.png": b"c"}))
    assert "fichier absent du nouveau : a/1/0/0-0.png" in problems
    assert "fichier en trop dans le nouveau : s/1/0/0-0.png" in problems


def test_a_removed_or_changed_index_field_is_found(tmp_path):
    new_index = json.loads(json.dumps(INDEX))
    del new_index["version"]
    new_index["layers"]["air"]["unit"] = "K"
    problems, _, _ = comparer.compare(make_site(tmp_path / "old", INDEX, TILES), make_site(tmp_path / "new", new_index, TILES))
    assert "index : version : retiré" in problems
    assert "index : layers.air.unit : 'C' devient 'K'" in problems


def test_the_command_line_exit_code(tmp_path, capsys):
    old, new = make_site(tmp_path / "old", INDEX, TILES), make_site(tmp_path / "new", INDEX, TILES)
    assert comparer.main([old, new]) == 0
    make_site(tmp_path / "new", INDEX, {"t/1/0/0-0.png": b"z"})
    assert comparer.main([old, new]) == 1
    assert "différence" in capsys.readouterr().out
