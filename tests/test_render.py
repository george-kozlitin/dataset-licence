import json
import re
from pathlib import Path

import pytest

from dlf.facts import DatasetFacts, Source
from dlf.render import DISCLAIMER, RenderStats, render_site

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
        "data/d/acme/permissive-set.json",
        "data/d/plain.json",
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
    ]:
        assert (root / rel).is_file(), rel


def test_disclaimer_on_every_dataset_page(site):
    root, _ = site
    pages = list((root / "d").rglob("index.html"))
    assert len(pages) == 3
    for p in pages:
        assert DISCLAIMER in p.read_text(encoding="utf-8")
    assert DISCLAIMER == "Licence facts reported by the dataset and its declared sources. Not legal advice, and not a grant of permission to use the dataset."


def test_forbidden_phrases_and_em_dash(tmp_path):
    neutral = [make(), make(id="b/c", owner="b", name="c", signal="unclear", stated_licence=None)]
    render_site(neutral, tmp_path, BASE, "2026-10-29", contact="a@b.test")
    for f in tmp_path.rglob("*"):
        if f.is_file():
            t = f.read_text(encoding="utf-8").lower()
            for bad in ("you may use", "allowed", "safe to use", "—"):
                assert bad not in t, (f, bad)


def test_skipped_ids(tmp_path):
    bad = [make(), make(id="x/<b>y", owner="x", name="<b>y"), make(id="a/..", owner="a", name="..")]
    stats = render_site(bad, tmp_path, BASE, "2026-10-29")
    assert stats.pages == 1 and stats.skipped_ids == 2
    for f in tmp_path.rglob("*"):
        if f.is_file():
            t = f.read_text(encoding="utf-8")
            assert "<b>y" not in t and "&lt;b&gt;y" not in t and "%3Cb" not in t, f


def test_stale_output_removed(tmp_path, facts):
    (tmp_path / "d/old").mkdir(parents=True)
    (tmp_path / "d/old/index.html").write_text("x")
    (tmp_path / "keep.txt").write_text("k")
    render_site(facts, tmp_path, BASE, "2026-10-29")
    assert not (tmp_path / "d/old").exists() and (tmp_path / "keep.txt").exists()


def test_hub_titles_and_slugs(tmp_path):
    fx = [make(stated_licence="???", id="o/a", owner="o", name="a"),
          make(stated_licence="Apache 2.0", id="o/b", owner="o", name="b",
               gated=True, tasks=["Question Answering"])]
    render_site(fx, tmp_path, BASE, "2026-10-29")
    assert "???" in read(tmp_path, "licence/unrecognised/index.html")
    assert "Apache 2.0" in read(tmp_path, "licence/apache-2-0/index.html")
    assert "Question Answering" in read(tmp_path, "task/question-answering/index.html")
    page = read(tmp_path, "d/o/b/index.html")
    assert "Access gated on Hugging Face: yes<" in page


def test_manual_gating(site):
    root, _ = site
    assert "yes, manual approval required" in read(root, "d/evil/set/index.html")


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
    j = json.loads(read(root, "data/d/evil/set.json"))
    assert j["citation"] == EVIL
    # the only script tag on any html page is the search script on the home page
    for f in root.rglob("*.html"):
        n = len(re.findall(r"<script", f.read_text(encoding="utf-8")))
        assert n == (1 if f == root / "index.html" else 0), f


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
    assert (
        '<link rel="canonical" href="https://example.test/d/acme/permissive-set/"'
        in read(root, "d/acme/permissive-set/index.html")
    )


def test_json_roundtrip(site, facts):
    root, _ = site
    idx = json.loads(read(root, "data/index.json"))
    assert idx == [[f.id, f.signal, f.stated_licence, f.tasks, f.downloads] for f in facts] or sorted(
        idx
    ) == sorted([[f.id, f.signal, f.stated_licence, f.tasks, f.downloads] for f in facts])
    full = json.loads(read(root, "data/d/acme/permissive-set.json"))
    assert full["id"] == "acme/permissive-set" and full["sources"] == []
    full = json.loads(read(root, "data/d/evil/set.json"))
    assert full["sources"][1] == {"id": "gone/missing", "licence": None, "found": False}


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
    many = [
        make(id=f"o/d{i}", owner="o", name=f"d{i}", downloads=1000 - i) for i in range(450)
    ]
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
    assert "max-width" in css and "prefers-color-scheme" in css
    assert len(read(root, "search.js").encode()) < 3072


def test_404_page(site):
    root, _ = site
    html = read(root, "404.html")
    assert "Page not found" in html
    assert '<meta name="robots" content="noindex">' in html
    assert 'href="/"' in html and 'href="/#q"' in html
    assert "canonical" not in html
    for f in root.glob("sitemap*.xml"):
        assert "404" not in f.read_text(encoding="utf-8")
