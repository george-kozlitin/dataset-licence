"""Render the static site: dataset pages, hubs, home, about, JSON data, sitemaps."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

from dlf import evidence, lineage
from dlf.facts import DatasetFacts

DISCLAIMER = "Licence facts reported by the dataset and its declared sources. Not legal advice, and not a grant of permission to use the dataset."
HUB_PAGE_SIZE = 200
SITEMAP_LIMIT = 5000
SIGNALS = ("permissive", "restrictive", "conflict", "unclear")
SIGNAL_LABELS = {
    "permissive": "Permissive or share-alike",
    "restrictive": "Use restrictions",
    "conflict": "Possible licence conflict",
    "unclear": "Unclear",
}
SIGNAL_TONES = {"permissive": "ok", "restrictive": "warn", "conflict": "bad", "unclear": "unk"}
CATEGORY_TONES = {"permissive": "ok", "share-alike": "ok", "restrictive": "warn", "unclear": "unk"}
HF = "https://huggingface.co/datasets/"
HERE = Path(__file__).parent
_SIMPLE_ID = re.compile(r"^[\w.-]+(/[\w.-]+)?$")
_SEGMENT = re.compile(r"^[A-Za-z0-9_.-]+$")
OWNED_DIRS = ("d", "licence", "signal", "task", "data")


@dataclass(frozen=True)
class RenderStats:
    pages: int
    hubs: int
    sitemap_files: int
    skipped_ids: int = 0


def slugify(text: str | None) -> str:
    """Lower-case [a-z0-9-] slug; empty input gives an empty string."""
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _segments(f: DatasetFacts) -> list[str] | None:
    parts = ([f.owner] if f.owner else []) + [f.name]
    if any(p in (".", "..") or not _SEGMENT.match(p) for p in parts):
        return None
    return parts


def _label(signal: str) -> str:
    return SIGNAL_LABELS.get(signal, SIGNAL_LABELS["unclear"])


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(HERE / "templates"),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["label"] = _label
    env.filters["ev"] = evidence.label
    env.globals["disclaimer"] = Markup(DISCLAIMER)  # constant, not third-party text
    env.globals["tones"] = SIGNAL_TONES
    env.globals["hf"] = lambda i: HF + "/".join(quote(p, safe="") for p in i.split("/"))
    return env


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _licence_slug(f: DatasetFacts) -> str:
    if not f.stated_licence:
        return "none"
    return slugify(f.stated_licence) or "unrecognised"


def render_site(
    facts: list[DatasetFacts],
    out: Path,
    base_url: str,
    built_at: str,
    contact: str = "",
    sitemap_limit: int = SITEMAP_LIMIT,
) -> RenderStats:
    out = Path(out)
    for d in OWNED_DIRS:
        shutil.rmtree(out / d, ignore_errors=True)
    base = base_url.rstrip("/")
    env = _env()
    ctx = {"built_at": built_at}
    sitemap_urls: list[tuple[str, str | None]] = []

    def page(rel_dir: str, template: str, lastmod: str | None = None, **kw) -> None:
        path = "/" + rel_dir.strip("/") + "/" if rel_dir.strip("/") else "/"
        html = env.get_template(template).render(canonical=base + path, **ctx, **kw)
        _write(out, (rel_dir.strip("/") + "/" if rel_dir.strip("/") else "") + "index.html", html)
        sitemap_urls.append((base + path, lastmod))

    # Usable datasets and their URL paths.
    items: list[tuple[DatasetFacts, str]] = []
    skipped = 0
    for f in sorted(facts, key=lambda x: (-x.downloads, x.id)):
        seg = _segments(f)
        if seg is not None:
            items.append((f, "d/" + "/".join(seg)))
        else:
            skipped += 1
    rendered = {f.id: "/" + p + "/" for f, p in items}

    for f, p in items:
        hit = lineage.conflict_source(f)
        sources = []
        for s in f.sources:
            cat = lineage.category(s.licence) if s.found else "unclear"
            sources.append(
                {
                    "id": s.id,
                    "licence": s.licence,
                    "found": s.found,
                    "evidence": s.evidence,
                    "href": rendered.get(s.id),
                    "hf": bool(_SIMPLE_ID.match(s.id)),
                    "hit": s.id == hit,
                    "category": lineage.CATEGORY_LABELS[cat],
                    "tone": CATEGORY_TONES[cat],
                }
            )
        page(
            p,
            "dataset.html.j2",
            lastmod=_date(f.last_modified),
            f=f,
            sources=sources,
            lineage=lineage.svg(f),
            modified=_human_date(f.last_modified),
            json_href="/data/" + p + ".json",
            licence_slug=_licence_slug(f),
            task_links=[(t, slugify(t)) for t in f.tasks if slugify(t)],
        )
        _write(out, "data/" + p + ".json", json.dumps(asdict(f), ensure_ascii=True, indent=1))

    index = [[f.id, f.signal, f.stated_licence, f.tasks, f.downloads, f.stated_evidence] for f, _ in items]
    _write(out, "data/index.json", json.dumps(index, ensure_ascii=True, separators=(",", ":")))

    # Hubs.
    names: dict[tuple[str, str], str] = {}
    groups: dict[str, dict[str, list]] = {"licence": {}, "signal": {s: [] for s in SIGNALS}, "task": {}}
    for f, p in items:
        entry = {"f": f, "href": "/" + p + "/"}
        ls = _licence_slug(f)
        names.setdefault(("licence", ls), f.stated_licence or "no licence declared")
        groups["licence"].setdefault(ls, []).append(entry)
        groups["signal"].setdefault(f.signal, []).append(entry)
        seen: set[str] = set()
        for t in f.tasks:
            ts = slugify(t)
            if ts and ts not in seen:
                seen.add(ts)
                names.setdefault(("task", ts), t)
                groups["task"].setdefault(ts, []).append(entry)
    hubs = 0
    for kind, by_key in groups.items():
        for key, entries in sorted(by_key.items()):
            pages_n = max(1, -(-len(entries) // HUB_PAGE_SIZE))
            title = _label(key) if kind == "signal" else names[(kind, key)]
            for n in range(1, pages_n + 1):
                rel = f"{kind}/{key}" + ("" if n == 1 else f"/page/{n}")
                chunk = entries[(n - 1) * HUB_PAGE_SIZE : n * HUB_PAGE_SIZE]
                page(
                    rel,
                    "hub.html.j2",
                    kind=kind,
                    key=key,
                    title=title,
                    entries=chunk,
                    total=len(entries),
                    n=n,
                    pages_n=pages_n,
                    base_path=f"/{kind}/{key}/",
                )
                hubs += 1

    # Home and about.
    totals = {s: len(groups["signal"][s]) for s in SIGNALS}
    conflicts = [
        dict(e, pair=lineage.conflict_pair(e["f"]), pair_ev=lineage.conflict_evidence(e["f"]))
        for e in groups["signal"]["conflict"][:20]
    ]
    featured = None
    # Prefer a conflict with a source, under a recognised declared licence: it reads best as a diagram.
    for e in sorted(conflicts, key=lambda e: lineage.conflict_source(e["f"]) is None):
        if e["pair"] and e["pair"][0] != "custom":
            a, b = e["pair"]
            if lineage.conflict_source(e["f"]):
                caption = f"Declared licence {a}; a declared source reports {b}"
            else:
                caption = f"Declared licence {a}; a repository licence file is detected as {b}"
            featured = dict(e, svg=lineage.svg(e["f"]), caption=caption)
            break
    examples = ([featured] if featured else []) + [
        {"f": f, "href": "/" + p + "/"} for f, p in items[:3] if not featured or f.id != featured["f"].id
    ][:2]
    page(
        "",
        "home.html.j2",
        totals=totals,
        signals=SIGNALS,
        conflicts=conflicts,
        featured=featured,
        examples=examples,
        total=len(items),
    )
    page("about", "about.html.j2", contact=contact)

    # Not-found page: served with status 404 by Cloudflare Pages for unknown paths.
    # Written directly, not through page(), so it stays out of the sitemap.
    _write(
        out,
        "404.html",
        env.get_template("404.html.j2").render(**ctx),
    )

    # Static files.
    for name in ("style.css", "search.js", "live.js"):
        shutil.copyfile(HERE / "static" / name, out / name)
    shutil.copytree(HERE / "static" / "fonts", out / "fonts", dirs_exist_ok=True)
    _write(out, "robots.txt", f"User-agent: *\nAllow: /\n\nSitemap: {base}/sitemap.xml\n")

    # Sitemaps.
    limit = max(1, sitemap_limit)
    chunks = [sitemap_urls[i : i + limit] for i in range(0, len(sitemap_urls), limit)] or [[]]
    for n, chunk in enumerate(chunks, 1):
        body = "".join(
            f"<url><loc>{xml_escape(u)}</loc>"
            + (f"<lastmod>{m}</lastmod>" if m else "")
            + "</url>\n"
            for u, m in chunk
        )
        _write(
            out,
            f"sitemap-{n}.xml",
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + body + "</urlset>\n",
        )
    body = "".join(
        f"<sitemap><loc>{xml_escape(base)}/sitemap-{n}.xml</loc></sitemap>\n" for n in range(1, len(chunks) + 1)
    )
    _write(
        out,
        "sitemap.xml",
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + body
        + "</sitemapindex>\n",
    )
    return RenderStats(
        pages=len(items), hubs=hubs, sitemap_files=len(chunks), skipped_ids=skipped
    )


def _date(value: str | None) -> str | None:
    m = re.match(r"^\d{4}-\d{2}-\d{2}", value or "")
    return m.group(0) if m else None


def _human_date(value: str | None) -> str | None:
    d = _date(value)
    if not d:
        return None
    y, m, day = d.split("-")
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{int(day)} {months[int(m) - 1]} {y}"
