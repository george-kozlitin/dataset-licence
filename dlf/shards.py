"""Shard keys and the shard file format, shared by the generator and the Pages worker (dlf/static/_worker.js).

A shard is a text file of lines "<lower-case id>\\t<JSON>\\n". The worker finds one line with indexOf and
parses only that line, so a shard can grow to megabytes without a full JSON.parse on each request.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from pathlib import Path

FACTS_SHARDS = 1024  # data/f/<xxx>.txt: facts of every dataset
PAGE_SHARDS = 4096  # p/<xxx>.txt: [id, html] of datasets beyond the static pages


def fnv1a(text: str) -> int:
    """32-bit FNV-1a over the UTF-8 bytes of text."""
    h = 0x811C9DC5
    for b in text.encode("utf-8"):
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def facts_shard(id: str) -> str:
    return f"{fnv1a(id.lower()) % FACTS_SHARDS:03x}"


def page_shard(id: str) -> str:
    return f"{fnv1a(id.lower()) % PAGE_SHARDS:03x}"


def line(id: str, value) -> str:
    """One shard line; JSON escapes tabs and newlines inside strings, so a line never splits."""
    return f"{id.lower()}\t{json.dumps(value, ensure_ascii=False, separators=(',', ':'))}\n"


def parse(text: str) -> dict[str, object]:
    """Values by lower-case ID from a shard's text; malformed lines are skipped."""
    out: dict[str, object] = {}
    for row in text.splitlines():
        key, sep, raw = row.partition("\t")
        if not sep:
            continue
        try:
            out[key] = json.loads(raw)
        except ValueError:
            continue
    return out


def write_facts(records: Iterable[dict], directory: Path) -> int:
    """Write facts records (each with an "id") into facts shards under directory; returns the file count.

    Existing shard files in directory are replaced, so the directory holds exactly these records.
    """
    groups: dict[str, list[str]] = {}
    for r in records:
        groups.setdefault(facts_shard(r["id"]), []).append(line(r["id"], r))
    directory.mkdir(parents=True, exist_ok=True)
    for old in list(directory.glob("*.txt")) + list(directory.glob("*.json")):
        old.unlink()
    for key, rows in groups.items():
        (directory / f"{key}.txt").write_text("".join(sorted(rows)), encoding="utf-8")
    return len(groups)


def read_facts(directory: Path) -> dict[str, dict]:
    """Facts by lower-case ID from facts shards: the line format (*.txt) or the earlier {id: facts} JSON (*.json).

    Unreadable shards are skipped with a warning; a record without an "id" is dropped.
    """
    out: dict[str, dict] = {}
    files = sorted(directory.glob("*.json")) + sorted(directory.glob("*.txt")) if directory.is_dir() else []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
            shard = json.loads(text) if f.suffix == ".json" else parse(text)
        except (OSError, ValueError):
            print(f"warning: unreadable cache shard {f.name}", file=sys.stderr)
            continue
        if isinstance(shard, dict):
            out.update({k: v for k, v in shard.items() if isinstance(v, dict) and "id" in v})
    return out
