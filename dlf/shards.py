"""Shard keys shared by the generator and the Pages worker (dlf/static/_worker.js)."""

from __future__ import annotations

FACTS_SHARDS = 256  # data/f/<xx>.json: facts of every dataset, keyed by lower-case ID
PAGE_SHARDS = 4096  # p/<xxx>.json: rendered pages of datasets beyond the static ones


def fnv1a(text: str) -> int:
    """32-bit FNV-1a over the UTF-8 bytes of text."""
    h = 0x811C9DC5
    for b in text.encode("utf-8"):
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def facts_shard(id: str) -> str:
    return f"{fnv1a(id.lower()) % FACTS_SHARDS:02x}"


def page_shard(id: str) -> str:
    return f"{fnv1a(id.lower()) % PAGE_SHARDS:03x}"
