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

from dlf import cut, evidence, lineage, shards
from dlf.facts import DatasetFacts
from dlf.shards import FACTS_SHARDS, PAGE_SHARDS, page_shard

DISCLAIMER = "Licence facts reported by the dataset and its declared sources. Not legal advice, and not a grant of permission to use the dataset."
HUB_PAGE_SIZE = 200
SITEMAP_LIMIT = 5000
STATIC_PAGES = 10000  # dataset pages written as files; the rest go into page shards
INDEX_LIMIT = 5000  # rows in data/index.json (find_datasets in the MCP Worker)
INDEXABLE_TOP = 5000  # the most downloaded stay indexable even when nothing was found (indexed since launch)
FILE_BUDGET = 19500  # Cloudflare Pages allows 20,000 files per deployment on the free plan
IDS_LIMIT = 50000  # IDs in data/ids.json (home page search box), most downloaded first: about 1.5 MB
# Pages per hub (licence, signal, task): the 2,000 most downloaded indexable datasets of each. At 800,000
# datasets the dataset pages, page and facts shards take 15,120 files and sitemaps about 170, which leaves
# about 4,200 for hubs; hubs number a few hundred keys, and only the few dozen large ones reach the cap.
HUB_MAX_PAGES = 10
PAGE_BUFFER = 32 * 1024 * 1024  # bytes of page-shard lines held before they are appended to their files
ASSETS = ("style.css", "search.js", "live.js", "motion.js", "_worker.js", "_routes.json")
# Files besides assets, fonts, dataset pages, shards, hubs and sitemaps: robots.txt, 404.html, home, about,
# data/index.json, data/ids.json, sitemap.xml.
FIXED_FILES = 7
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
OWNED_DIRS = ("d", "licence", "signal", "task", "data", "p")


@dataclass(frozen=True)
class RenderStats:
    pages: int
    hubs: int
    sitemap_files: int
    skipped_ids: int = 0
    static_pages: int = 0
    noindex: int = 0
    files: int = 0


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


def words(text: str) -> Markup:
    """Text split into words, each in a span the headline animation lifts in turn."""
    return Markup(" ").join(
        Markup('<span class="w" style="--i:{}">{}</span>').format(i, w) for i, w in enumerate(text.split())
    )


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
    env.globals["words"] = words
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


def thin(f: DatasetFacts) -> bool:
    """Nothing about the licence was found: such pages are kept out of search engines."""
    return (
        f.stated_from == "none"
        and not f.licence_name
        and not f.licence_files
        and not f.sources
        and not f.card_nc
    )


class _ShardAppender:
    """Page-shard lines buffered per shard and appended to p/<xxx>.txt once the buffers pass a size.

    Peak memory is bounded by the buffer size, not by the number of pages.
    """

    def __init__(self, directory: Path, limit: int) -> None:
        self.directory, self.limit = directory, limit
        self.buffers: dict[str, list[str]] = {}
        self.size = 0
        self.files: set[str] = set()

    def add(self, id: str, html: str) -> None:
        row = shards.line(id, [id, html])
        self.buffers.setdefault(page_shard(id), []).append(row)
        self.size += len(row)
        if self.size >= self.limit:
            self.flush()

    def flush(self) -> None:
        if self.buffers:
            self.directory.mkdir(parents=True, exist_ok=True)
        for key, rows in self.buffers.items():
            with (self.directory / f"{key}.txt").open("a", encoding="utf-8") as fh:
                fh.write("".join(rows))
            self.files.add(key)
        self.buffers, self.size = {}, 0


def hub_pages(listed: int) -> int:
    """Pages of a hub that lists this many datasets: at least one, at most HUB_MAX_PAGES."""
    return min(HUB_MAX_PAGES, max(1, -(-listed // HUB_PAGE_SIZE)))


def planned_files(
    datasets: int, static_pages: int, hub_listed: list[int], sitemap_urls: int, sitemap_limit: int = SITEMAP_LIMIT,
) -> int:
    """Upper bound of the files a build writes, from counts alone (checked before rendering).

    hub_listed holds, per hub key, how many indexable datasets the hub could list.
    """
    hubs = sum(hub_pages(n) for n in hub_listed)
    fonts = sum(1 for x in (HERE / "static" / "fonts").rglob("*") if x.is_file())
    static = min(datasets, static_pages)
    rest = datasets - static
    sitemaps = max(1, -(-(sitemap_urls + hubs) // max(1, sitemap_limit))) + 1  # + the sitemap index
    return (
        static + min(rest, PAGE_SHARDS) + min(datasets, FACTS_SHARDS) + hubs + sitemaps
        + len(ASSETS) + fonts + FIXED_FILES
    )


def render_site(
    facts: list[DatasetFacts],
    out: Path,
    base_url: str,
    built_at: str,
    contact: str = "",
    sitemap_limit: int = SITEMAP_LIMIT,
    static_pages: int = STATIC_PAGES,
    file_budget: int = FILE_BUDGET,
) -> RenderStats:
    out = Path(out)
    for d in OWNED_DIRS:
        shutil.rmtree(out / d, ignore_errors=True)
    base = base_url.rstrip("/")
    env = _env()
    ctx = {"built_at": built_at}
    sitemap_urls: list[tuple[str, str | None]] = []

    def page(
        rel_dir: str, template: str, lastmod: str | None = None, keep: dict | None = None,
        index: bool = True, **kw,
    ) -> None:
        """Render a page to its file, or into keep (a page shard) when given."""
        path = "/" + rel_dir.strip("/") + "/" if rel_dir.strip("/") else "/"
        html = env.get_template(template).render(canonical=base + path, **ctx, **kw)
        if keep is not None:
            keep["html"] = html
        else:
            _write(out, (rel_dir.strip("/") + "/" if rel_dir.strip("/") else "") + "index.html", html)
        if index:
            sitemap_urls.append((base + path, lastmod))

    # Usable datasets and their URL paths, most downloaded first.
    items: list[tuple[DatasetFacts, str]] = []
    skipped = 0
    for f in sorted(facts, key=lambda x: (-x.downloads, x.id)):
        seg = _segments(f)
        if seg is not None:
            items.append((f, "d/" + "/".join(seg)))
        else:
            skipped += 1
    rendered = {f.id: "/" + p + "/" for f, p in items}
    hidden = [i >= INDEXABLE_TOP and thin(f) for i, (f, _) in enumerate(items)]

    # Hub groups: every dataset counts in the totals, only indexable ones are listed.
    names: dict[tuple[str, str], str] = {}
    groups: dict[str, dict[str, list]] = {"licence": {}, "signal": {s: [] for s in SIGNALS}, "task": {}}
    for (f, p), h in zip(items, hidden, strict=True):
        entry = {"f": f, "href": "/" + p + "/", "hidden": h}
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
    cap = HUB_MAX_PAGES * HUB_PAGE_SIZE
    listed = {
        (kind, key): [e for e in entries if not e["hidden"]][:cap]
        for kind, by_key in groups.items()
        for key, entries in by_key.items()
    }

    # Fail before the long render when the deployment would exceed the file limit.
    plan = planned_files(
        len(items), static_pages, [len(v) for v in listed.values()],
        sitemap_urls=hidden.count(False) + 2, sitemap_limit=sitemap_limit,
    )
    if plan > file_budget:
        raise RuntimeError(f"{plan} files planned in {out}, above the budget of {file_budget}")

    pages_out = _ShardAppender(out / "p", PAGE_BUFFER)
    noindex = 0
    for i, ((f, p), h) in enumerate(zip(items, hidden, strict=True)):
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
        keep: dict | None = None if i < static_pages else {}
        noindex += h
        page(
            p,
            "dataset.html.j2",
            lastmod=_date(f.last_modified),
            keep=keep,
            index=not h,
            noindex=h,
            f=f,
            sources=sources,
            lineage=lineage.svg(f),
            modified=_human_date(f.last_modified),
            json_href="/data/" + p + ".json",
            licence_slug=_licence_slug(f),
            task_links=[(t, slugify(t)) for t in f.tasks if slugify(t)],
        )
        if keep is not None:
            pages_out.add(f.id, keep["html"])
    pages_out.flush()
    del rendered

    # Facts of every dataset: served as /data/d/<id>.json by _worker.js, and the next build's cache.
    shards.write_facts((asdict(f) for f, _ in items), out / "data" / "f")
    compact = {"ensure_ascii": True, "separators": (",", ":")}
    index = [
        [f.id, f.signal, f.stated_licence, f.tasks, f.downloads, f.stated_evidence]
        for f, _ in items[:INDEX_LIMIT]
    ]
    _write(out, "data/index.json", json.dumps(index, **compact))
    _write(out, "data/ids.json", json.dumps([f.id for f, _ in items[:IDS_LIMIT]], **compact))

    # Hubs: the most downloaded indexable datasets of each key, at most HUB_MAX_PAGES pages.
    hubs = 0
    for kind, by_key in groups.items():
        for key, entries in sorted(by_key.items()):
            shown = listed[(kind, key)]
            pages_n = hub_pages(len(shown))
            title = _label(key) if kind == "signal" else names[(kind, key)]
            for n in range(1, pages_n + 1):
                rel = f"{kind}/{key}" + ("" if n == 1 else f"/page/{n}")
                chunk = shown[(n - 1) * HUB_PAGE_SIZE : n * HUB_PAGE_SIZE]
                page(
                    rel,
                    "hub.html.j2",
                    index=bool(chunk),
                    noindex=not chunk,
                    kind=kind,
                    key=key,
                    title=title,
                    entries=chunk,
                    total=len(entries),
                    listed=len(shown),
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
        cut=cut.svg(len(items)),
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
    for name in ASSETS:
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
    files = sum(1 for x in out.rglob("*") if x.is_file())
    if files > file_budget:
        raise RuntimeError(f"{files} files in {out}, above the budget of {file_budget}")
    return RenderStats(
        pages=len(items), hubs=hubs, sitemap_files=len(chunks), skipped_ids=skipped,
        static_pages=min(len(items), static_pages), noindex=noindex, files=files,
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
