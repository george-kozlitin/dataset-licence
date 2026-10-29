"""Build the licence facts for one dataset from its Hub entry, card and sources."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from dlf.hub import Hub
from dlf.licences import classify, normalise

CITATION_CAP = 1500
IGNORED_SOURCES = frozenset({"original", "extended"})
LICENCE_FILE_RE = re.compile(r"^(licen[cs]e|copying)", re.IGNORECASE)
NC_PHRASES = (
    "non-commercial",
    "noncommercial",
    "research only",
    "research purposes only",
    "research-only",
    "non commercial",
    "not for commercial use",
    "academic use only",
)
NC_STAND_IN = "cc-by-nc-4.0"
NC_REASON = (
    "the card's licence text appears to restrict use to non-commercial or research purposes"
)


@dataclass(frozen=True)
class Source:
    id: str
    licence: str | None
    found: bool


@dataclass(frozen=True)
class DatasetFacts:
    id: str
    owner: str | None
    name: str
    downloads: int
    likes: int
    last_modified: str
    stated_licence: str | None
    stated_from: str  # "tag" | "card" | "none"
    licence_name: str | None
    licence_link: str | None
    file_licence: str | None
    licence_files: list[str] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    gated: bool | str = False
    tasks: list[str] = field(default_factory=list)
    citation: str | None = None
    signal: str = "unclear"
    reason: str = ""


# -- licence text detection -----------------------------------------------

_SPDX_RE = re.compile(r"SPDX-License-Identifier:\s*([A-Za-z0-9.+\-]+)", re.IGNORECASE)
_CC_LONG_RE = re.compile(
    r"Attribution(-NonCommercial)?(-ShareAlike|-NoDerivatives|-NoDerivs)?\s+(\d\.\d)",
    re.IGNORECASE,
)
_CC_SHORT_RE = re.compile(r"\bCC[ -]BY(-NC)?(-SA|-ND)?[ -](\d\.\d)", re.IGNORECASE)
_GPL_VER_RE = re.compile(r"version\s+([23])", re.IGNORECASE)


def _cc(nc: str | None, rest: str | None, ver: str) -> str:
    rest = (rest or "").lower().lstrip("-")
    rest = {"sharealike": "sa", "noderivatives": "nd", "noderivs": "nd"}.get(rest, rest)
    parts = ["cc-by"] + (["nc"] if nc else []) + ([rest] if rest else []) + [ver]
    return "-".join(parts)


def detect_licence_text(text: str | None) -> str | None:
    """Licence id from SPDX-style header phrases in the start of a LICENSE file."""
    if not text:
        return None
    head = text[:2048]
    low = head.lower()
    m = _SPDX_RE.search(head)
    if m:
        return normalise(m.group(1))
    if "apache license" in low and re.search(r"version\s+2\.0", low):
        return "apache-2.0"
    if "creative commons zero" in low or "cc0 1.0" in low:
        return "cc0-1.0"
    m = _CC_LONG_RE.search(head)
    if m:
        return _cc(m.group(1), m.group(2), m.group(3))
    m = _CC_SHORT_RE.search(head)
    if m:
        return _cc(m.group(1), m.group(2), m.group(3))
    if "gnu affero general public license" in low:
        return "agpl-3.0"
    if "gnu lesser general public license" in low:
        return "lgpl-3.0"
    if "gnu general public license" in low:
        v = _GPL_VER_RE.search(head)
        return f"gpl-{v.group(1)}.0" if v else None
    if "redistribution and use in source and binary forms" in low:
        return "bsd-3-clause" if "neither the name" in low else "bsd-2-clause"
    if low.lstrip().startswith("mit license") or "permission is hereby granted, free of charge" in low:
        return "mit"
    return None


# -- card parsing ----------------------------------------------------------


def _front_matter(card: str | None) -> dict | None:
    if not card:
        return None
    m = re.match(r"﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", card, re.DOTALL)
    if not m:
        return None
    try:
        data = yaml.safe_load(m.group(1))
    except (yaml.YAMLError, ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _body(card: str | None) -> str:
    if not card:
        return ""
    m = re.match(r"﻿?---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|$)", card, re.DOTALL)
    return card[m.end():] if m else card


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def _sections(body: str) -> list[tuple[str, str]]:
    """(heading text, section text) pairs; headings inside code fences are ignored."""
    out: list[tuple[str, list[str]]] = []
    fence = False
    for line in body.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        m = None if fence else _HEADING_RE.match(line)
        if m:
            out.append((m.group(2), []))
        elif out:
            out[-1][1].append(line)
    return [(h, "\n".join(lines)) for h, lines in out]


def _section_text(body: str, pattern: str) -> str:
    rx = re.compile(pattern, re.IGNORECASE)
    return "\n".join(t for h, t in _sections(body) if rx.search(h))


_FENCE_RE = re.compile(r"```[ \t]*([A-Za-z]*)[^\n]*\n(.*?)```", re.DOTALL)


def _citation(body: str) -> str | None:
    text: str | None = None
    for m in _FENCE_RE.finditer(body):
        lang, code = m.group(1).lower(), m.group(2).strip()
        if lang == "bibtex" or (not lang and code.startswith("@")):
            text = code
            break
    if not text:
        section = _section_text(body, r"citation|attribution").strip()
        text = section or None
    if not text:
        return None
    return text[:CITATION_CAP]


# -- helpers ---------------------------------------------------------------


def _as_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [v for v in value if isinstance(v, str)]
    return []


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _tag_licence(tags: object) -> str | None:
    for tag in _as_list(tags):
        if tag.lower().startswith("license:"):
            lic = normalise(tag.split(":", 1)[1])
            if lic:
                return lic
    return None


def _entry_licence(e: dict, card_data: dict) -> tuple[str | None, str]:
    lic = _tag_licence(e.get("tags"))
    if lic:
        return lic, "tag"
    lic = normalise(card_data.get("license"))
    if lic:
        return lic, "card"
    return None, "none"


def _source_ids(card_data: dict, own_id: str) -> list[str]:
    out: list[str] = []
    for raw in _as_list(card_data.get("source_datasets")):
        s = raw.strip()
        if s.lower().startswith("extended|"):
            s = s.split("|", 1)[1].strip()
        if not s or s.lower() in IGNORED_SOURCES or "://" in s:
            continue
        if s.lower() == own_id.lower() or s in out:
            continue
        out.append(s)
    return out


_SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)?$")


def _valid_source_id(sid: str) -> bool:
    return bool(_SOURCE_ID_RE.match(sid)) and all(p not in (".", "..") for p in sid.split("/"))


def _custom_nc(stated: str | None, licence_name: str | None, body: str) -> bool:
    if stated not in (None, "other", "unknown"):
        return False
    haystack = f"{licence_name or ''}\n{_section_text(body, r'licen[cs]e')}".lower()
    return any(_has_unnegated(haystack, p) for p in NC_PHRASES)


def _has_unnegated(haystack: str, phrase: str) -> bool:
    start = 0
    while (i := haystack.find(phrase, start)) != -1:
        before = re.findall(r"[a-z']+", haystack[:i])[-3:]
        if "not" not in before and "no" not in before:
            return True
        start = i + 1
    return False


def _count(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


# -- builder ---------------------------------------------------------------


def build_facts(entry: dict, hub: Hub) -> DatasetFacts:
    """Facts for one list entry. Card problems never raise; a HubError propagates."""
    id_ = entry["id"]
    owner, _, name = id_.rpartition("/")
    card = hub.card(id_)
    card_data = _front_matter(card)
    if card_data is None:
        cd = entry.get("cardData")
        card_data = cd if isinstance(cd, dict) else {}
    body = _body(card)

    stated, stated_from = _entry_licence(entry, card_data)
    licence_name = _str_or_none(card_data.get("license_name"))
    licence_link = _str_or_none(card_data.get("license_link"))

    siblings = entry.get("siblings")
    listing = Hub.files_from_entry(entry) if isinstance(siblings, list) else hub.files(id_)
    files = [
        f for f in (listing or [])
        if isinstance(f, str) and "/" not in f and LICENCE_FILE_RE.match(f)
    ]
    file_licence = None
    for f in files:
        file_licence = detect_licence_text(hub.raw(id_, f))
        if file_licence:
            break

    sources: list[Source] = []
    for sid in _source_ids(card_data, id_):
        found = hub.dataset(sid) if _valid_source_id(sid) else None
        if found is None:
            sources.append(Source(sid, None, False))
            continue
        cd = found.get("cardData")
        lic, _ = _entry_licence(found, cd if isinstance(cd, dict) else {})
        sources.append(Source(sid, lic, True))

    custom_nc = _custom_nc(stated, licence_name, body)
    signal, reason = classify(
        NC_STAND_IN if custom_nc else stated, [s.licence for s in sources], file_licence
    )
    if custom_nc:
        if signal == "restrictive":
            reason = NC_REASON
        else:
            reason = reason.replace(f"the tag {NC_STAND_IN}", "the custom non-commercial licence")

    gated = entry.get("gated", False)
    if not isinstance(gated, (bool, str)):
        gated = False

    return DatasetFacts(
        id=id_,
        owner=owner or None,
        name=name,
        downloads=_count(entry.get("downloads")),
        likes=_count(entry.get("likes")),
        last_modified=str(entry.get("lastModified") or ""),
        stated_licence=stated,
        stated_from=stated_from,
        licence_name=licence_name,
        licence_link=licence_link,
        file_licence=file_licence,
        licence_files=files,
        sources=sources,
        gated=gated,
        tasks=_as_list(card_data.get("task_categories")),
        citation=_citation(body),
        signal=signal,
        reason=reason,
    )
