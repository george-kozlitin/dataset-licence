"""Build the licence facts for one dataset from its Hub entry, card and sources."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from dlf import evidence
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
    "The dataset card's licence name or licence section contains wording that suggests "
    "non-commercial, research-only or academic-only use restrictions."
)


@dataclass(frozen=True)
class Source:
    id: str
    licence: str | None
    found: bool
    evidence: str | None = None  # where the licence was read, pinned to the source's revision


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
    revision: str | None = None  # Hub commit the facts were read at
    stated_evidence: str | None = None  # card or API record behind stated_licence
    file_evidence: str | None = None  # licence file read, or the file list that was checked
    card_nc: bool = False  # card wording suggests a custom non-commercial licence


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


def build_facts(entry: dict, hub: Hub, known: dict[str, dict] | None = None) -> DatasetFacts:
    """Facts for one list entry. Card problems never raise; a HubError propagates.

    known maps lower-case IDs to entries of the same listing; a source found there is not
    fetched again.
    """
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
    rev = _str_or_none(entry.get("sha"))
    file_licence = None
    file_read = files[0] if files else None
    for f in files:
        file_licence = detect_licence_text(hub.raw(id_, f))
        if file_licence:
            file_read = f
            break
    file_ev = evidence.file_url(id_, rev, file_read) if file_read else evidence.tree_url(id_, rev)
    if card is not None:
        stated_ev = evidence.card_url(id_, rev)
    elif stated_from != "none":
        stated_ev = evidence.api_url(id_, rev)
    else:
        stated_ev = evidence.tree_url(id_, rev)

    sources = [_lookup_source(sid, hub, known or {}) for sid in _source_ids(card_data, id_)]

    custom_nc = _custom_nc(stated, licence_name, body)
    signal, reason = _signal(stated, custom_nc, sources, file_licence)

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
        revision=rev,
        stated_evidence=stated_ev,
        file_evidence=file_ev,
        card_nc=custom_nc,
    )


def _lookup_source(sid: str, hub: Hub | None, known: dict[str, dict]) -> Source:
    record = known.get(sid.lower())
    if record is None and hub is not None and _valid_source_id(sid):
        record = hub.dataset(sid)
    if record is None:
        return Source(sid, None, False, evidence.page_url(sid) if _valid_source_id(sid) else None)
    cd = record.get("cardData")
    lic, _ = _entry_licence(record, cd if isinstance(cd, dict) else {})
    return Source(sid, lic, True, source_evidence(record, sid))


def _signal(
    stated: str | None, card_nc: bool, sources: list[Source], file_licence: str | None
) -> tuple[str, str]:
    signal, reason = classify(
        NC_STAND_IN if card_nc else stated, [s.licence for s in sources], file_licence
    )
    if card_nc:
        if signal == "restrictive":
            reason = NC_REASON
        else:
            reason = reason.replace(
                f"The declared licence ({NC_STAND_IN})",
                "The custom licence described in the dataset card",
            )
    return signal, reason


def refresh_facts(cached: dict, entry: dict, known: dict[str, dict] | None = None) -> DatasetFacts:
    """Facts from a previous run's JSON, with the listing's counts and fresh source licences.

    Card-derived values are reused, so this is only right while the dataset's revision is
    unchanged (or as a stale fallback). Sources in the listing get their current licence;
    other sources keep the licence recorded last time.
    """
    known = known or {}
    sources = []
    for s in cached.get("sources") or []:
        sid = s["id"]
        if sid.lower() in known:
            sources.append(_lookup_source(sid, None, known))
        else:
            sources.append(Source(sid, s.get("licence"), bool(s.get("found")), s.get("evidence")))
    stated = cached.get("stated_licence")
    card_nc = bool(cached.get("card_nc"))
    signal, reason = _signal(stated, card_nc, sources, cached.get("file_licence"))
    gated = entry.get("gated", cached.get("gated", False))
    if not isinstance(gated, (bool, str)):
        gated = False
    keep = {k: cached.get(k) for k in (
        "stated_licence", "stated_from", "licence_name", "licence_link", "file_licence",
        "citation", "revision", "stated_evidence", "file_evidence",
    )}
    owner, _, name = cached["id"].rpartition("/")
    return DatasetFacts(
        id=cached["id"],
        owner=owner or None,
        name=name,
        downloads=_count(entry.get("downloads")),
        likes=_count(entry.get("likes")),
        last_modified=str(entry.get("lastModified") or cached.get("last_modified") or ""),
        stated_from=keep.pop("stated_from") or "none",
        licence_files=list(cached.get("licence_files") or []),
        sources=sources,
        gated=gated,
        tasks=list(cached.get("tasks") or []),
        signal=signal,
        reason=reason,
        card_nc=card_nc,
        **keep,
    )


def source_evidence(record: dict, sid: str) -> str:
    """Evidence for a source's licence: its card when the record lists one, else its API record."""
    rev = _str_or_none(record.get("sha"))
    canonical = _str_or_none(record.get("id")) or sid  # legacy aliases redirect to owner/name
    names = Hub.files_from_entry(record)
    if "README.md" in names or not isinstance(record.get("siblings"), list):
        return evidence.card_url(canonical, rev)
    return evidence.api_url(canonical, rev)
