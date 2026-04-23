import json
from pathlib import Path

from dit.utils.jsonl import read_rows, write_rows


def test_read_rows(sample_jsonl: Path):
    rows = list(read_rows(sample_jsonl))
    assert len(rows) == 3
    assert rows[0]["messages"][0]["content"] == "Implement an LRU cache in Python"


def test_read_rows_preserves_order(tmp_path: Path):
    fp = tmp_path / "test.jsonl"
    data = [{"id": i} for i in range(100)]
    with open(fp, "w") as f:
        for d in data:
            f.write(json.dumps(d) + "\n")
    rows = list(read_rows(fp))
    assert [r["id"] for r in rows] == list(range(100))


def test_write_rows(tmp_path: Path):
    fp = tmp_path / "out.jsonl"
    data = [{"a": 1}, {"b": 2}]
    write_rows(fp, data)
    rows = list(read_rows(fp))
    assert rows == data


def test_read_rows_skips_blank_lines(tmp_path: Path):
    fp = tmp_path / "test.jsonl"
    fp.write_text('{"a":1}\n\n{"b":2}\n\n')
    rows = list(read_rows(fp))
    assert len(rows) == 2


def test_write_rows_no_trailing_newline_per_row(tmp_path: Path):
    fp = tmp_path / "out.jsonl"
    write_rows(fp, [{"x": 1}])
    content = fp.read_text()
    assert content.endswith("\n")
    assert content.count("\n") == 1
