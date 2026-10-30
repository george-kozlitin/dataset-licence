import json
import re
from pathlib import Path

import pytest

from dlf import shards
from dlf.facts import DatasetFacts, Source
from dlf.render import DISCLAIMER, RenderStats, render_site, thin
from dlf.shards import facts_shard, page_shard

BASE = "https://example.test"
EVIL = 'x<script>alert("1")</script>\' & more'


def make(**kw):
    d: dict = dict(  # noqa: C408
        id="acme/permissive-set",
        owner="acme",
        name="permissive-set",
        downloads=500,
        likes=3,
        last_modified="2026-09-01T10:00:00.000Z",
        stated_licence="apache-2.0",
        stated_from="tag",
        licence_name=None,
        licence_link=None,
        file_licence="apache-2.0",
        licence_files=["LICENSE"],
        sources=[],
        gated=False,
        tasks=["text-classification"],
        citation=None,
        signal="permissive",
        reason="tag and file agree",
    )
    d.update(kw)
    return DatasetFacts(**d)


@pytest.fixture
def facts():
    return [
        make(),
        make(
            id="evil/set",
            owner="evil",
            name="set",
            downloads=900,
            stated_licence="mit",
            file_licence="cc-by-nc-4.0",
            sources=[
                Source(id="acme/permissive-set", licence="apache-2.0", found=True),
                Source(id="gone/missing", licence=None, found=False),
            ],
            gated="manual",
            tasks=["text-classification", "Question Answering"],
            citation=EVIL,
            signal="conflict",
            reason="tag mit, file cc-by-nc-4.0",
        ),
        make(
            id="plain",
            owner=None,
            name="plain",
            downloads=10,
            stated_licence=None,
            stated_from="none",
            file_licence=None,
            licence_files=[],
            tasks=[],
            signal="unclear",
            reason="no licence stated",
        ),
    ]


@pytest.fixture
def site(tmp_path, facts):
    stats = render_site(facts, tmp_path, BASE, "2026-10-29", contact="me@example.test")
    return tmp_path, stats


def read(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8")


def facts_json(root: Path, id: str) -> dict:
    """What _worker.js serves at /data/d/<id>.json."""
    return shards.parse(read(root, f"data/f/{facts_shard(id)}.txt"))[id.lower()]


def test_stats(site):
    _, stats = site
    assert isinstance(stats, RenderStats)
    assert stats.pages == 3
    assert stats.hubs >= 6
    assert stats.sitemap_files == 1


def test_exact_files_exist(site):
    root, _ = site
    for rel in [
        "index.html",
        "about/index.html",
        "d/acme/permissive-set/index.html",
        "d/plain/index.html",
        "data/index.json",
        "data/ids.json",
        "_worker.js",
        "_routes.json",
        "licence/apache-2-0/index.html",
        "licence/none/index.html",
        "signal/conflict/index.html",
        "task/text-classification/index.html",
        "task/question-answering/index.html",
        "sitemap.xml",
        "sitemap-1.xml",
        "robots.txt",
        "404.html",
        "style.css",
        "search.js",
        "live.js",
        "fonts/archivo.woff2",
        "fonts/jbmono-400.woff2",
        "fonts/jbmono-500.woff2",
        "fonts/OFL.txt",
    ]:
        assert (root / rel).is_file(), rel


def test_disclaimer_on_every_dataset_page(site):
    root, _ = site
    pages = list((root / "d").rglob("index.html"))
    assert len(pages) == 3
    for p in pages:
        assert DISCLAIMER in p.read_text(encoding="utf-8")
    assert (
        DISCLAIMER
        == "Licence facts reported by the dataset and its declared sources. Not legal advice, and not a grant of permission to use the dataset."
    )


def test_forbidden_phrases_and_em_dash(tmp_path):
    neutral = [make(), make(id="b/c", owner="b", name="c", signal="unclear", stated_licence=None)]
    render_site(neutral, tmp_path, BASE, "2026-10-29", contact="a@b.test")
    for f in tmp_path.rglob("*"):
        if f.is_file() and f.parent.name != "fonts":  # binary font files and their licence
            t = f.read_text(encoding="utf-8").lower()
            for bad in ("you may use", "allowed", "safe to use", "—"):
                assert bad not in t, (f, bad)


def test_skipped_ids(tmp_path):
    bad = [make(), make(id="x/<b>y", owner="x", name="<b>y"), make(id="a/..", owner="a", name="..")]
    stats = render_site(bad, tmp_path, BASE, "2026-10-29")
    assert stats.pages == 1 and stats.skipped_ids == 2
    for f in tmp_path.rglob("*"):
        if f.is_file() and f.parent.name != "fonts":  # binary font files and their licence
            t = f.read_text(encoding="utf-8")
            assert "<b>y" not in t and "&lt;b&gt;y" not in t and "%3Cb" not in t, f


def test_stale_output_removed(tmp_path, facts):
    (tmp_path / "d/old").mkdir(parents=True)
    (tmp_path / "d/old/index.html").write_text("x")
    (tmp_path / "keep.txt").write_text("k")
    render_site(facts, tmp_path, BASE, "2026-10-29")
    assert not (tmp_path / "d/old").exists() and (tmp_path / "keep.txt").exists()


def test_hub_titles_and_slugs(tmp_path):
    fx = [
        make(stated_licence="???", id="o/a", owner="o", name="a"),
        make(
            stated_licence="Apache 2.0",
            id="o/b",
            owner="o",
            name="b",
            gated=True,
            tasks=["Question Answering"],
        ),
    ]
    render_site(fx, tmp_path, BASE, "2026-10-29")
    assert "???" in read(tmp_path, "licence/unrecognised/index.html")
    assert "Apache 2.0" in read(tmp_path, "licence/apache-2-0/index.html")
    assert "Question Answering" in read(tmp_path, "task/question-answering/index.html")
    page = read(tmp_path, "d/o/b/index.html")
    assert "<span>Access gated on Hugging Face</span><span>Yes</span>" in page


def test_manual_gating(site):
    root, _ = site
    assert "Yes, manual approval required" in read(root, "d/evil/set/index.html")


def test_escaping(site):
    root, _ = site
    for f in root.rglob("*.html"):
        t = f.read_text(encoding="utf-8")
        assert "<script>alert" not in t, f
    page = read(root, "d/evil/set/index.html")
    assert "<pre>" in page
    assert "&lt;script&gt;" in page
    assert "&#34;" in page or "&quot;" in page
    # JSON keeps the raw text but is valid JSON
    j = facts_json(root, "evil/set")
    assert j["citation"] == EVIL
    # script tags appear only on the home page (live check, search) and the 404 page (live check)
    expected = {root / "index.html": 2, root / "404.html": 1}
    for f in root.rglob("*.html"):
        n = len(re.findall(r"<script", f.read_text(encoding="utf-8")))
        assert n == expected.get(f, 0), f


def test_links_and_evidence(site):
    root, _ = site
    page = read(root, "d/evil/set/index.html")
    assert "https://huggingface.co/datasets/evil/set" in page
    assert 'href="/licence/mit/"' in page
    assert 'href="/signal/conflict/"' in page
    assert 'href="/task/question-answering/"' in page
    assert 'href="/d/acme/permissive-set/"' in page  # rendered source
    assert 'href="https://huggingface.co/datasets/gone/missing"' not in page  # not found
    assert "not found" in page
    assert "Possible licence conflict" in page
    assert "LICENSE" in page and "cc-by-nc-4.0" in page
    assert "manual" in page
    none_page = read(root, "d/plain/index.html")
    assert 'href="/licence/none/"' in none_page
    assert "Unclear" in none_page


def test_signal_labels(site):
    root, _ = site
    assert "Permissive or share-alike" in read(root, "d/acme/permissive-set/index.html")
    assert "Use restrictions" in read(root, "signal/restrictive/index.html")


def test_canonical_and_meta(site):
    root, _ = site
    for f in root.rglob("*.html"):
        t = f.read_text(encoding="utf-8")
        m = re.search(r'<link rel="canonical" href="([^"]+)"', t)
        if f.name == "404.html":
            assert not m
            continue
        assert m, f
        assert m.group(1).startswith(BASE + "/") and m.group(1).endswith("/"), (f, m.group(1))
        assert "<title>" in t and 'name="description"' in t
        assert 'name="viewport"' in t
    assert '<link rel="canonical" href="https://example.test/d/acme/permissive-set/"' in read(
        root, "d/acme/permissive-set/index.html"
    )


def test_json_roundtrip(site, facts):
    root, _ = site
    idx = json.loads(read(root, "data/index.json"))
    assert idx == [
        [f.id, f.signal, f.stated_licence, f.tasks, f.downloads, f.stated_evidence] for f in facts
    ] or sorted(idx) == sorted(
        [[f.id, f.signal, f.stated_licence, f.tasks, f.downloads, f.stated_evidence] for f in facts]
    )
    full = facts_json(root, "acme/permissive-set")
    assert full["id"] == "acme/permissive-set" and full["sources"] == []
    full = facts_json(root, "evil/set")
    assert full["sources"][1] == {"id": "gone/missing", "licence": None, "found": False, "evidence": None}


def test_home(site):
    root, _ = site
    t = read(root, "index.html")
    assert 'id="q"' in t and "/search.js" in t
    assert "Possible licence conflict" in t
    assert 'href="/about/"' in t
    assert "evil/" in t  # top conflicts


def test_about_contact(site, tmp_path_factory, facts):
    root, _ = site
    assert 'href="mailto:me@example.test"' in read(root, "about/index.html")
    other = tmp_path_factory.mktemp("o")
    render_site(facts, other, BASE, "2026-10-29")
    assert "mailto:" not in read(other, "about/index.html")


def test_sitemap_split_and_lastmod(tmp_path, facts):
    stats = render_site(facts, tmp_path, BASE, "2026-10-29", sitemap_limit=4)
    assert stats.sitemap_files > 1
    index = read(tmp_path, "sitemap.xml")
    assert "<sitemapindex" in index
    locs = re.findall(r"<loc>([^<]+)</loc>", index)
    assert len(locs) == stats.sitemap_files
    urls = []
    for i in range(1, stats.sitemap_files + 1):
        t = read(tmp_path, f"sitemap-{i}.xml")
        found = re.findall(r"<loc>([^<]+)</loc>", t)
        assert 0 < len(found) <= 4
        urls += found
    assert len(urls) == len(set(urls))
    assert BASE + "/d/acme/permissive-set/" in urls
    assert BASE + "/" in urls
    all_xml = "".join(read(tmp_path, f"sitemap-{i}.xml") for i in range(1, stats.sitemap_files + 1))
    assert "<lastmod>2026-09-01</lastmod>" in all_xml
    assert "<script" not in all_xml


def test_robots(site):
    root, _ = site
    t = read(root, "robots.txt")
    assert "Sitemap: https://example.test/sitemap.xml" in t


def test_hub_pagination(tmp_path):
    many = [make(id=f"o/d{i}", owner="o", name=f"d{i}", downloads=1000 - i) for i in range(450)]
    render_site(many, tmp_path, BASE, "2026-10-29")
    p1 = read(tmp_path, "licence/apache-2-0/index.html")
    p3 = read(tmp_path, "licence/apache-2-0/page/3/index.html")
    assert p1.count('href="/d/o/') == 200
    assert p3.count('href="/d/o/') == 50
    assert not (tmp_path / "licence/apache-2-0/page/4").exists()
    assert 'href="/licence/apache-2-0/page/2/"' in p1
    assert 'rel="canonical" href="https://example.test/licence/apache-2-0/page/3/"' in p3


def test_css_and_js(site):
    root, _ = site
    css = read(root, "style.css")
    assert "max-width" in css and "prefers-reduced-motion" in css and "/fonts/archivo.woff2" in css
    assert len(read(root, "search.js").encode()) < 3072


def test_404_page(site):
    root, _ = site
    html = read(root, "404.html")
    assert "Page not found" in html
    assert '<meta name="robots" content="noindex">' in html
    assert 'href="/"' in html and 'href="/#q"' in html
    assert "canonical" not in html
    # unknown /d/<id>/ addresses offer a live check of that ID
    assert 'id="live-404"' in html and 'id="live"' in html and '<script src="/live.js"' in html
    for f in root.glob("sitemap*.xml"):
        assert "404" not in f.read_text(encoding="utf-8")


def test_dataset_page_has_lineage_and_hit_row(site):
    root, _ = site
    page = read(root, "d/evil/set/index.html")
    assert 'class="g-desk"' in page and 'class="g-mob"' in page
    assert "Licence lineage of evil/set" in page
    # the conflict comes from the licence file, so no source row is highlighted
    assert 'class="hit"' not in page
    assert "Red marks the licence the signal is based on." in page


def test_home_featured_and_pairs(tmp_path):
    fx = [
        make(),
        make(
            id="o/conf",
            owner="o",
            name="conf",
            downloads=800,
            file_licence=None,
            licence_files=[],
            sources=[
                Source(id="acme/permissive-set", licence="apache-2.0", found=True),
                Source(id="o/sa", licence="cc-by-sa-4.0", found=True),
            ],
            signal="conflict",
            reason="The declared licence (apache-2.0) may conflict with a source dataset's reported share-alike licence (cc-by-sa-4.0).",
        ),
    ]
    render_site(fx, tmp_path, BASE, "2026-10-30")
    home = read(tmp_path, "index.html")
    assert "Declared licence apache-2.0; a declared source reports cc-by-sa-4.0" in home
    assert 'apache-2.0<span class="x" aria-label="and">×</span>cc-by-sa-4.0' in home
    page = read(tmp_path, "d/o/conf/index.html")
    assert page.count('class="hit"') == 1
    assert (
        '<tr class="hit"><td><a href="https://huggingface.co/datasets/o/sa">o/sa</a></td>' in page
    )


def test_evidence_links_on_pages_hubs_and_index(tmp_path):
    sha = "ec5f1f46e9af79639a90684a7a70a956c4998f04"
    card = f"https://huggingface.co/datasets/acme/permissive-set/blob/{sha}/README.md"
    tree = f"https://huggingface.co/datasets/acme/permissive-set/tree/{sha}"
    src = "https://huggingface.co/datasets/o/src/blob/main/README.md"
    f = make(
        revision=sha,
        stated_evidence=card,
        file_evidence=tree,
        licence_files=[],
        file_licence=None,
        sources=[Source(id="o/src", licence="mit", found=True, evidence=src)],
    )
    render_site([f], tmp_path, BASE, "2026-10-30")
    page = read(tmp_path, "d/acme/permissive-set/index.html")
    assert f'<a class="ev" href="{card}">Evidence: README.md at ec5f1f4</a>' in page
    assert f'<a class="ev" href="{tree}">Evidence: file list at ec5f1f4</a>' in page
    assert f'<a class="ev" href="{src}">Evidence: README.md</a>' in page
    assert f'<a href="{card}">' in page  # the diagram's declared-licence box
    hub = read(tmp_path, "licence/apache-2-0/index.html")
    assert f'<a class="lic" href="{card}"' in hub
    index = json.loads(read(tmp_path, "data/index.json"))
    assert index[0][5] == card


def test_home_offers_live_check(site):
    root, _ = site
    home = read(root, "index.html")
    assert '<div id="live" aria-live="polite"></div>' in home
    assert home.index('src="/live.js"') < home.index('src="/search.js"')
    js = read(root, "live.js")
    assert "https://mcp.datalicense.uk/v1/check?id=" in js and "—" not in js


def test_pages_beyond_the_static_limit_go_to_shards(tmp_path):
    fx = [make(id=f"o/d{i}", owner="o", name=f"d{i}", downloads=100 - i) for i in range(5)]
    stats = render_site(fx, tmp_path, BASE, "2026-10-30", static_pages=2)
    assert stats.static_pages == 2 and stats.pages == 5
    assert (tmp_path / "d/o/d0/index.html").is_file() and (tmp_path / "d/o/d1/index.html").is_file()
    assert not (tmp_path / "d/o/d2").exists()
    shard = shards.parse(read(tmp_path, f"p/{page_shard('o/d3')}.txt"))
    canonical, html = shard["o/d3"]
    assert canonical == "o/d3" and "<h1>o/d3</h1>" in html and DISCLAIMER in html
    # every page is in the sitemap and in ids.json; facts of every dataset are in the shards
    sitemap = read(tmp_path, "sitemap-1.xml")
    assert f"{BASE}/d/o/d4/" in sitemap
    assert json.loads(read(tmp_path, "data/ids.json")) == [f"o/d{i}" for i in range(5)]
    assert facts_json(tmp_path, "O/D4")["id"] == "o/d4"


def test_index_json_is_capped(tmp_path, monkeypatch):
    import dlf.render as r

    monkeypatch.setattr(r, "INDEX_LIMIT", 2)
    fx = [make(id=f"o/d{i}", owner="o", name=f"d{i}", downloads=100 - i) for i in range(4)]
    render_site(fx, tmp_path, BASE, "2026-10-30")
    assert [row[0] for row in json.loads(read(tmp_path, "data/index.json"))] == ["o/d0", "o/d1"]


def test_thin_pages_are_noindex_and_out_of_the_sitemap(tmp_path, monkeypatch):
    import dlf.render as r

    monkeypatch.setattr(r, "INDEXABLE_TOP", 1)
    empty = make(id="o/empty", owner="o", name="empty", stated_licence=None, stated_from="none",
                 licence_files=[], file_licence=None, sources=[], signal="unclear")  # fmt: skip
    assert thin(empty) and not thin(make())
    assert not thin(make(stated_licence=None, stated_from="none", licence_files=["LICENSE"]))
    top = make(id="o/top", owner="o", name="top", downloads=10_000, stated_licence=None,
               stated_from="none", licence_files=[], file_licence=None, sources=[], signal="unclear")  # fmt: skip
    stats = render_site([make(), empty, top], tmp_path, BASE, "2026-10-30")
    assert stats.noindex == 1
    assert "noindex" not in read(tmp_path, "d/o/top/index.html")  # within the indexable top
    assert '<meta name="robots" content="noindex">' in read(tmp_path, "d/o/empty/index.html")
    assert "noindex" not in read(tmp_path, "d/acme/permissive-set/index.html")
    sitemap = read(tmp_path, "sitemap-1.xml")
    assert "/d/o/empty/" not in sitemap and "/d/acme/permissive-set/" in sitemap


def test_file_budget(tmp_path):
    with pytest.raises(RuntimeError, match="above the budget"):
        render_site([make()], tmp_path, BASE, "2026-10-30", file_budget=5)


def test_shard_files_use_the_line_format(tmp_path, monkeypatch):
    import dlf.render as r

    monkeypatch.setattr(r, "PAGE_BUFFER", 1)  # append every page as it is rendered
    fx = [make(id=f"o/D{i}", owner="o", name=f"D{i}", downloads=100 - i) for i in range(40)]
    render_site(fx, tmp_path, BASE, "2026-10-30", static_pages=5)
    pages: dict = {}
    for f in (tmp_path / "p").iterdir():
        assert re.fullmatch(r"[0-9a-f]{3}\.txt", f.name), f.name
        text = f.read_text(encoding="utf-8")
        assert text.endswith("\n") and all("\t" in row for row in text.splitlines())
        for key, value in shards.parse(text).items():
            assert f.name == page_shard(key) + ".txt" and key == value[0].lower()
            pages[key] = value
    assert sorted(pages) == sorted(f"o/d{i}" for i in range(5, 40))
    assert pages["o/d7"][0] == "o/D7" and "<h1>o/D7</h1>" in pages["o/d7"][1]
    facts = {}
    for f in (tmp_path / "data/f").iterdir():
        assert re.fullmatch(r"[0-9a-f]{3}\.txt", f.name), f.name
        for key, value in shards.parse(f.read_text(encoding="utf-8")).items():
            assert f.name == facts_shard(key) + ".txt"
            facts[key] = value
    assert len(facts) == 40 and facts["o/d0"]["id"] == "o/D0"
    assert not list(tmp_path.rglob("p/*.json")) and not list(tmp_path.rglob("data/f/*.json"))


def test_page_shards_are_appended_not_held(tmp_path):
    from dlf.render import _ShardAppender

    out = _ShardAppender(tmp_path / "p", limit=100)
    out.add("o/a", "x" * 10)
    assert not (tmp_path / "p").exists()  # below the limit: buffered
    out.add("o/b", "y" * 200)
    assert out.size == 0 and not out.buffers  # past the limit: written out
    out.add("o/c", "z")
    out.flush()
    lines = {}
    for f in (tmp_path / "p").iterdir():
        lines.update(shards.parse(f.read_text(encoding="utf-8")))
    assert lines == {"o/a": ["o/a", "x" * 10], "o/b": ["o/b", "y" * 200], "o/c": ["o/c", "z"]}


def test_stale_shards_of_both_formats_removed(tmp_path, facts):
    for rel in ("p/abc.json", "p/abc.txt", "data/f/0a.json", "data/f/0ab.txt"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("{}")
    render_site(facts, tmp_path, BASE, "2026-10-29")
    for rel in ("p/abc.json", "p/abc.txt", "data/f/0a.json", "data/f/0ab.txt"):
        assert not (tmp_path / rel).exists() or rel == f"data/f/{facts_shard('plain')}.txt", rel
    assert sorted(x.name for x in (tmp_path / "data/f").iterdir()) == sorted(
        {facts_shard(f.id) + ".txt" for f in facts}
    )


def test_ids_json_is_capped(tmp_path, monkeypatch):
    import dlf.render as r

    monkeypatch.setattr(r, "IDS_LIMIT", 3)
    fx = [make(id=f"o/d{i}", owner="o", name=f"d{i}", downloads=100 - i) for i in range(6)]
    render_site(fx, tmp_path, BASE, "2026-10-30")
    assert json.loads(read(tmp_path, "data/ids.json")) == ["o/d0", "o/d1", "o/d2"]
    assert facts_json(tmp_path, "o/d5")["id"] == "o/d5"  # facts of every dataset stay reachable


def test_hubs_list_indexable_datasets_up_to_the_cap(tmp_path, monkeypatch):
    import dlf.render as r

    monkeypatch.setattr(r, "HUB_PAGE_SIZE", 2)
    monkeypatch.setattr(r, "HUB_MAX_PAGES", 3)
    monkeypatch.setattr(r, "INDEXABLE_TOP", 2)
    fx = [make(id=f"o/d{i}", owner="o", name=f"d{i}", downloads=100 - i) for i in range(9)]
    empty = {"stated_licence": None, "stated_from": "none", "licence_files": [], "file_licence": None,
             "sources": [], "signal": "unclear", "tasks": ["rare-task"]}  # fmt: skip
    fx += [make(id=f"o/t{i}", owner="o", name=f"t{i}", downloads=50 - i, **empty) for i in range(3)]
    stats = render_site(fx, tmp_path, BASE, "2026-10-30")
    # 9 indexable apache-2.0 datasets, 2 per page, capped at 3 pages: the 6 most downloaded
    hub = "licence/apache-2-0"
    pages = [read(tmp_path, f"{hub}/index.html")] + [
        read(tmp_path, f"{hub}/page/{n}/index.html") for n in (2, 3)
    ]
    listed = [re.findall(r'class="id" href="/d/(o/d\d)/"', p) for p in pages]
    assert listed == [["o/d0", "o/d1"], ["o/d2", "o/d3"], ["o/d4", "o/d5"]]
    assert not (tmp_path / f"{hub}/page/4").exists()
    assert 'rel="next" href="/licence/apache-2-0/page/3/"' in pages[1]
    assert 'rel="next"' not in pages[2] and "Page 3 of 3." in pages[2]
    # thin datasets beyond the indexable top are not listed; a hub left empty is not indexed
    none = read(tmp_path, "licence/none/index.html")
    assert "/d/o/t" not in none and '<meta name="robots" content="noindex">' in none
    assert "/d/o/t" not in read(tmp_path, "task/rare-task/index.html")
    assert "noindex" not in pages[0]
    sitemap = read(tmp_path, "sitemap-1.xml")
    assert f"{BASE}/licence/none/" not in sitemap and f"{BASE}/{hub}/page/3/" in sitemap
    assert stats.hubs == len(list(tmp_path.glob("licence/**/index.html"))) + len(
        list(tmp_path.glob("signal/**/index.html"))
    ) + len(list(tmp_path.glob("task/**/index.html")))


def test_file_budget_at_full_scale():
    """800,000 datasets: a cheap stand-in for the hub keys, shaped like the live site (183 keys at 50,000)."""
    from dlf.render import FILE_BUDGET, HUB_MAX_PAGES, STATIC_PAGES, hub_pages, planned_files

    n = 800_000
    # Zipf-like sizes: a few huge keys (signals, common licences and tasks), a long tail of small ones.
    hub_listed = [n // 4] * 4 + [max(1, n // (k * k)) for k in range(1, 1500)]
    files = planned_files(n, STATIC_PAGES, hub_listed, sitemap_urls=n)
    assert files < FILE_BUDGET, files
    assert hub_pages(n) == HUB_MAX_PAGES and hub_pages(0) == 1 and hub_pages(201) == 2
    # the fixed part alone: static pages, every page and facts shard, sitemaps of every page
    assert planned_files(n, STATIC_PAGES, [], sitemap_urls=n) < 15_400


def test_file_budget_is_checked_before_rendering(tmp_path, monkeypatch):
    import dlf.render as r

    calls = []
    monkeypatch.setattr(r.lineage, "svg", lambda f: calls.append(f) or "")
    with pytest.raises(RuntimeError, match="files planned"):
        render_site([make()], tmp_path, BASE, "2026-10-30", file_budget=5)
    assert not calls
