"""Lineage diagram for a dataset page: the dataset, its declared licence, its licence file and its sources."""

from __future__ import annotations

from dataclasses import dataclass

from markupsafe import Markup, escape

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
    )
    if f.licence_files:
        file_node = Node(
            normalise(f.file_licence) or "licence not identified",
            "licence file",
            "bad" if _file_conflict(f) else _tone(category(f.file_licence)),
        )
    else:
        file_node = Node("no licence file", "found at top level", "")
    hit = conflict_source(f)
    ordered = sorted(f.sources, key=lambda s: s.id != hit)
    shown = ordered if len(ordered) <= MAX_SOURCES else ordered[: MAX_SOURCES - 1]
    srcs = [
        Node(
            s.id,
            (normalise(s.licence) or "no licence in metadata") if s.found else "not found on the Hub",
            "bad" if s.id == hit else _tone(category(s.licence)),
        )
        for s in shown
    ]
    if len(ordered) > len(shown):
        srcs.append(Node(f"+{len(ordered) - len(shown)} sources", "listed below", ""))
    return declared, file_node, srcs


def _short(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _box(x: float, y: float, w: float, node: Node, n: int, core: bool = False) -> str:
    cls = "node core" if core else ("node " + node.tone).strip()
    title = escape(node.title)
    parts = [f'<g class="{cls}"><title>{title}</title>']
    if core:
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="44" rx="8"/>')
        parts.append(f'<text x="{x + w / 2}" y="{y + 27}" text-anchor="middle">{escape(_short(node.title, n))}</text>')
    else:
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="48" rx="8"/>')
        parts.append(f'<text x="{x + 14}" y="{y + 21}">{escape(_short(node.title, n))}</text>')
        parts.append(f'<text class="sub" x="{x + 14}" y="{y + 38}">{escape(_short(node.sub, n + 4))}</text>')
    parts.append("</g>")
    return "".join(parts)


def _edge(d: str, tone: str, delay: float) -> str:
    cls = ("edge " + tone).strip()
    return f'<path class="{cls}" d="{d}" style="animation-delay:{delay:.1f}s"/>'


def svg(f: DatasetFacts) -> Markup:
    """Two diagrams: a radial one for wide screens and a vertical one for phones."""
    declared, file_node, srcs = nodes(f)
    core = Node(f.id, "", "")
    label = escape(f"Licence lineage of {f.id}")

    # Wide: declared top left, licence file bottom centre, sources on the right and left.
    cx, cy = 260, 190
    slots = [(360, 40), (360, 166), (360, 292), (20, 166)]
    wide = [_edge(f"M{cx} {cy} C 200 120, 140 90, 90 64", declared.tone, 0.2)]
    boxes = [_box(20, 40, 140, declared, 16)]
    for i, node in enumerate(srcs):
        x, y = slots[i]
        ex = x + 70
        wide.append(_edge(f"M{cx} {cy} C {(cx + ex) / 2} {cy}, {ex} {y + 24}, {ex} {y + 24}", node.tone, 0.3 + i * 0.1))
        boxes.append(_box(x, y, 140, node, 16))
    wide.append(_edge(f"M{cx} {cy} C {cx - 10} 260, {cx - 10} 290, {cx} 318", file_node.tone, 0.7))
    boxes.append(_box(180, 318, 160, file_node, 18))
    boxes.append(_box(cx - 80, cy - 22, 160, core, 18, core=True))
    desk = (
        f'<svg class="g-desk" viewBox="0 0 520 380" role="img" aria-label="{label}">'
        + "".join(wide + boxes)
        + "</svg>"
    )

    # Phone: declared, dataset, sources in a 2-column grid, licence file.
    rows = -(-len(srcs) // 2) if srcs else 0
    top_src = 236
    file_y = top_src + rows * 64 + (30 if rows else 0)
    height = file_y + 62
    medges = [_edge("M160 66 V 116", declared.tone, 0.2)]
    mboxes = [_box(70, 16, 180, declared, 18), _box(70, 116, 180, core, 18, core=True)]
    for i, node in enumerate(srcs):
        col, row = i % 2, i // 2
        x = 8 if col == 0 else 166
        y = top_src + row * 64
        ex = x + 73
        medges.append(_edge(f"M160 160 C 160 200, {ex} 200, {ex} {y}", node.tone, 0.3 + i * 0.1))
        mboxes.append(_box(x, y, 146, node, 14))
    medges.append(_edge(f"M160 160 V {file_y}", file_node.tone, 0.7))
    mboxes.append(_box(70, file_y, 180, file_node, 18))
    mob = (
        f'<svg class="g-mob" viewBox="0 0 320 {height}" role="img" aria-label="{label}">'
        + "".join(medges + mboxes)
        + "</svg>"
    )
    return Markup(desk + mob)
