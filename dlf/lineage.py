"""Lineage diagram for a dataset page: the dataset, its declared licence, its licence file and its sources."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from markupsafe import Markup, escape

from dlf import evidence
from dlf.facts import DatasetFacts
from dlf.licences import _category, normalise

MAX_SOURCES = 4
CATEGORY_LABELS = {
    "permissive": "Permissive",
    "share-alike": "Share-alike",
    "restrictive": "Use restrictions",
    "unclear": "Unclear",
}


@dataclass(frozen=True)
class Node:
    title: str
    sub: str
    tone: str  # "ok", "warn", "bad" or "" (neutral)
    href: str | None = None  # evidence link


def category(licence: str | None) -> str:
    """Category of a raw licence string: permissive, share-alike, restrictive or unclear."""
    return _category(normalise(licence))


def _tone(cat: str) -> str:
    return {"permissive": "ok", "share-alike": "ok", "restrictive": "warn"}.get(cat, "")


def conflict_source(f: DatasetFacts) -> str | None:
    """ID of the source the conflict signal is based on, if the signal comes from a source."""
    if f.signal != "conflict" or _file_conflict(f):
        return None
    cat = _declared_category(f)
    for s in f.sources:
        scat = category(s.licence)
        if scat == "restrictive" and cat != "restrictive":
            return s.id
        if scat == "share-alike" and cat == "permissive":
            return s.id
    return None


def conflict_pair(f: DatasetFacts) -> tuple[str, str] | None:
    """(declared side, other side) of a conflict, as licence identifiers, for lists."""
    if f.signal != "conflict":
        return None
    left = "custom" if "custom licence" in f.reason else (normalise(f.stated_licence) or "custom")
    if _file_conflict(f):
        return left, normalise(f.file_licence) or ""
    sid = conflict_source(f)
    for s in f.sources:
        if s.id == sid:
            return left, normalise(s.licence) or ""
    return None


def conflict_evidence(f: DatasetFacts) -> tuple[str | None, str | None]:
    """Evidence links for the two sides of conflict_pair()."""
    if f.signal != "conflict":
        return None, None
    if _file_conflict(f):
        return f.stated_evidence, f.file_evidence
    sid = conflict_source(f)
    other = next((s.evidence for s in f.sources if s.id == sid), None)
    return f.stated_evidence, other


def _declared_category(f: DatasetFacts) -> str:
    cat = category(f.stated_licence)
    # A custom licence read from the card as non-commercial is classed as restrictive.
    if cat == "unclear" and (f.signal == "restrictive" or "custom licence" in f.reason):
        return "restrictive"
    return cat


def _file_conflict(f: DatasetFacts) -> bool:
    """Mirrors classify(): a recognised licence file in a different category than the declared one."""
    if f.signal != "conflict":
        return False
    fcat = category(f.file_licence)
    return fcat != "unclear" and fcat != _declared_category(f)


def nodes(f: DatasetFacts) -> tuple[Node, Node, list[Node]]:
    """(declared licence, licence file, sources) nodes; the conflict side is tone 'bad'."""
    dcat = _declared_category(f)
    declared = Node(
        "custom licence" if "custom licence" in f.reason else (normalise(f.stated_licence) or "not declared"),
        "declared licence",
        _tone(dcat),
        f.stated_evidence,
    )
    if f.licence_files:
        file_node = Node(
            normalise(f.file_licence) or "licence not identified",
            "licence file",
            "bad" if _file_conflict(f) else _tone(category(f.file_licence)),
            f.file_evidence,
        )
    else:
        file_node = Node("no licence file", "found at top level", "", f.file_evidence)
    hit = conflict_source(f)
    ordered = sorted(f.sources, key=lambda s: s.id != hit)
    shown = ordered if len(ordered) <= MAX_SOURCES else ordered[: MAX_SOURCES - 1]
    srcs = [
        Node(
            s.id,
            (normalise(s.licence) or "no licence in metadata") if s.found else "not found on the Hub",
            "bad" if s.id == hit else _tone(category(s.licence)),
            s.evidence,
        )
        for s in shown
    ]
    if len(ordered) > len(shown):
        srcs.append(Node(f"+{len(ordered) - len(shown)} sources", "listed below", ""))
    return declared, file_node, srcs


def _short(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _go(url: str | None) -> str:
    """Hover label of a cell: the file behind it without the commit, or that there is nothing to open."""
    if not url:
        return "nothing to open"
    return re.sub(r" at [0-9a-f]{7}$", "", evidence.label(url))


def _hexpts(cx: float, cy: float, r: float) -> str:
    return " ".join(
        f"{cx + r * math.cos(math.pi / 3 * i):.1f},{cy + r * math.sin(math.pi / 3 * i):.1f}" for i in range(6)
    )


COLOURS = {"ok": "#4f7f63", "bad": "#a4495a", "warn": "#a27a3c", "": "#b9b7c0"}
INK = "#45394f"


def _board(f: DatasetFacts, cells: list[tuple[str, Node]], desk: bool) -> str:
    """One diagram: the dataset as an ink hex in the middle, the cells around it, an edge from each cell."""
    sfx = "d" if desk else "m"
    emb, face = f"lin-emb-{sfx}", f"lin-face-{sfx}"
    srcs = cells[2:]
    if desk:
        w, h, c, r = 560, 430, (280, 205), 62
        pos = [(95, 70), (280, 370)] + [(470, 70), (470, 205), (470, 340), (95, 205), (95, 340)][: len(srcs)]
    else:
        w, c, r = 340, (170, 150), 50
        rows = -(-len(srcs) // 2)
        pos = [(170, 46)]
        for i in range(len(srcs)):
            last_alone = i == len(srcs) - 1 and len(srcs) % 2
            pos.append((170 if last_alone else (70, 270)[i % 2], 270 + (i // 2) * 110))
        file_y = 270 + rows * 110 + (15 if rows else 30)
        pos.insert(1, (170, file_y))
        h = file_y + 55
    label = escape(f"Licence lineage of {f.id}")
    out = [(
        f'<svg class="lineage" viewBox="0 0 {w} {h}" role="img" aria-label="{label}"><defs>'
        f'<filter id="{emb}" x="-30%" y="-30%" width="160%" height="160%">'
        '<feDropShadow dx="4" dy="5" stdDeviation="5" flood-color="#46506a" flood-opacity=".22"/>'
        '<feDropShadow dx="-3" dy="-3" stdDeviation="4" flood-color="#fff" flood-opacity=".95"/></filter>'
        f'<linearGradient id="{face}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f7f8f9"/>'
        '<stop offset="1" stop-color="#e3e5e9"/></linearGradient></defs>'
    )]
    for i, ((k, node), (nx, ny)) in enumerate(zip(cells, pos, strict=True)):
        d = f"M{nx} {ny} L{c[0]} {c[1]}"
        kind = "solid" if node.tone else "dashed"
        live = " live" if node.tone == "bad" else ""
        width = 2 if node.tone == "bad" else 1.3
        out.append(
            f'<path class="edge {kind}" data-k="{escape(k)}" style="--d:{0.25 + i * 0.08:.2f}s" pathLength="1" '
            f'd="{d}" stroke="{COLOURS[node.tone]}" stroke-width="{width}" fill="none"/>'
            f'<path class="flow{live}" data-k="{escape(k)}" d="{d}" '
            f'stroke="{COLOURS[node.tone] if node.tone else "#8a8794"}" stroke-width="2.6" fill="none"/>'
        )
    # The dataset ID shrinks to fit the core hex, then is cut short; the full ID is in its title.
    room = 128 if desk else 104
    fs = max(11.0, min(17.0 if desk else 15.0, room / (0.56 * max(1, len(f.id)))))
    core_text = _short(f.id, int(room / (0.56 * fs)))
    out.append(
        f'<g class="core"><title>{escape(f.id)}</title>'
        f'<polygon points="{_hexpts(c[0], c[1], r + 14)}" fill="{INK}" filter="url(#{emb})"/>'
        f'<text x="{c[0]}" y="{c[1] + 6}" text-anchor="middle" font-family="Jost, sans-serif" font-weight="500" '
        f'font-size="{fs:.1f}" fill="#f6f7f8">{escape(core_text)}</text></g>'
    )
    for i, ((k, node), (nx, ny)) in enumerate(zip(cells, pos, strict=True)):
        bad = node.tone == "bad"
        stroke = f' stroke="{COLOURS["bad"]}" stroke-width="2"' if bad else ""
        attrs = f'class="node{" hit" if bad else ""}" data-k="{escape(k)}" style="--d:{0.45 + i * 0.08:.2f}s"'
        head = f'<a {attrs} href="{escape(node.href)}">' if node.href else f"<g {attrs}>"
        out.append(
            head + f"<title>{escape(node.title)}: {escape(node.sub)}</title><g class=\"lift\">"
            f'<polygon points="{_hexpts(nx, ny, r - 12)}" fill="url(#{face})" filter="url(#{emb})"{stroke}/>'
            f'<text x="{nx}" y="{ny - 1}" text-anchor="middle" font-family="Jost, sans-serif" font-weight="500" '
            f'font-size="{13.5 if desk else 12.5}" fill="{COLOURS["bad"] if bad else INK}">'
            f"{escape(_short(node.title, 14))}</text>"
            f'<text class="sub" x="{nx}" y="{ny + 16}" text-anchor="middle" font-family="Jost, sans-serif" '
            f'font-size="{11 if desk else 10.5}" fill="#86838f">{escape(_short(node.sub, 18))}</text>'
            f'<text class="go" x="{nx}" y="{ny + 16}" text-anchor="middle" font-family="Jost, sans-serif" '
            f'font-weight="500" font-size="{10.5 if desk else 10}" fill="{COLOURS[node.tone] if node.tone else INK}">'
            f"{escape(_short(_go(node.href), 22))}{' ↗' if node.href else ''}</text>"
            "</g>" + ("</a>" if node.href else "</g>")
        )
    out.append("</svg>")
    return "".join(out)


def svg(f: DatasetFacts) -> Markup:
    """Two diagrams, one for wide screens and one for phones (CSS shows one). SVG ids differ between them:
    with a shared id the hidden diagram's defs would be used, and the visible one would lose its fills."""
    declared, file_node, srcs = nodes(f)
    cells = [("decl", declared), ("file", file_node)]
    more = len(f.sources) > MAX_SOURCES
    cells += [("more" if more and i == len(srcs) - 1 else s.title, s) for i, s in enumerate(srcs)]
    return Markup(
        f'<div class="desk-only">{_board(f, cells, True)}</div><div class="mob-only">{_board(f, cells, False)}</div>'
    )
