import re

from dlf import lineage
from dlf.facts import DatasetFacts, Source
from dlf.lineage import conflict_pair, conflict_source, nodes, svg


def make(**kw):
    d = dict(  # noqa: C408
        id="o/d", owner="o", name="d", downloads=1, likes=0, last_modified="2026-01-01",
        stated_licence="apache-2.0", stated_from="tag", licence_name=None, licence_link=None,
        file_licence=None, licence_files=[], sources=[], signal="permissive", reason="",
    )
    d.update(kw)
    return DatasetFacts(**d)


def test_source_conflict_is_found_and_listed_first():
    f = make(
        sources=[Source("a", "mit", True), Source("b", "cc-by-sa-4.0", True), Source("c", "cc-by-nc-4.0", True)],
        signal="conflict",
    )
    assert conflict_source(f) == "b"
    assert conflict_pair(f) == ("apache-2.0", "cc-by-sa-4.0")
    _, _, srcs = nodes(f)
    assert srcs[0].title == "b" and srcs[0].tone == "bad"


def test_file_conflict_marks_the_file_node():
    f = make(stated_licence="cc-by-4.0", file_licence="cc-by-nc-4.0", licence_files=["LICENSE"], signal="conflict")
    assert conflict_source(f) is None
    assert conflict_pair(f) == ("cc-by-4.0", "cc-by-nc-4.0")
    _, file_node, _ = nodes(f)
    assert file_node.tone == "bad"


def test_custom_licence_side_is_named_custom():
    f = make(
        stated_licence="other", file_licence="cc-by-sa-3.0", licence_files=["LICENSE"], signal="conflict",
        reason="The custom licence described in the dataset card and the licence detected in a repository file (cc-by-sa-3.0) fall into different categories, so the licences may conflict.",
    )
    assert conflict_pair(f) == ("custom", "cc-by-sa-3.0")
    declared, _, _ = nodes(f)
    assert declared.title == "custom licence"


def test_more_than_four_sources_collapse():
    f = make(sources=[Source(f"s{i}", "mit", True) for i in range(7)])
    _, _, srcs = nodes(f)
    assert len(srcs) == 4 and srcs[-1].title == "+4 sources"


def test_no_pair_without_conflict():
    assert conflict_pair(make()) is None


def test_svg_escapes_ids():
    f = make(id='o/<script>"x', name='<script>"x', sources=[Source("<b>", "mit", True)])
    out = str(svg(f))
    assert "<script>" not in out and "<b>" not in out
    assert "&lt;script&gt;" in out


def test_nodes_link_to_evidence_and_pair_evidence():
    ev = "https://huggingface.co/datasets/o/d/blob/abc/README.md"
    src_ev = "https://huggingface.co/datasets/b/blob/def/README.md"
    f = make(
        stated_evidence=ev,
        file_evidence="https://huggingface.co/datasets/o/d/tree/abc",
        sources=[Source("b", "cc-by-sa-4.0", True, src_ev)],
        signal="conflict",
    )
    out = str(svg(f))
    assert f'data-k="decl" style="--d:0.45s" href="{ev}">' in out
    assert f'data-k="b" style="--d:0.61s" href="{src_ev}">' in out
    assert 'href="https://huggingface.co/datasets/o/d/tree/abc"' in out
    assert lineage.conflict_evidence(f) == (ev, src_ev)
    assert lineage.conflict_evidence(make()) == (None, None)


def test_two_diagrams_with_their_own_svg_ids():
    out = str(svg(make(sources=[Source("b", "mit", True)])))
    ids = re.findall(r' id="([^"]+)"', out)
    assert sorted(ids) == ["lin-emb-d", "lin-emb-m", "lin-face-d", "lin-face-m"]
    assert out.count("url(#lin-face-d)") == 3 and out.count("url(#lin-face-m)") == 3  # decl, file, source


def test_cells_carry_keys_hover_labels_and_flows():
    ev = "https://huggingface.co/datasets/o/d/blob/0123456789abcdef0123456789abcdef01234567/README.md"
    f = make(stated_evidence=ev, sources=[Source(f"s{i}", "mit", True) for i in range(6)])
    out = str(svg(f))
    for k in ("decl", "file", "s0", "s1", "s2", "more"):
        assert out.count(f'class="node" data-k="{k}"') == 2, k
        assert out.count(f'class="edge solid" data-k="{k}"') + out.count(f'class="edge dashed" data-k="{k}"') == 2
        assert out.count(f'data-k="{k}" d="M') == 2  # the flow path
    assert out.count('pathLength="1"') == 12
    assert out.count(">README.md ↗</text>") == 2 and "README.md at" not in out  # the commit is left out
    assert ">nothing to open</text>" in out  # the licence file cell has no evidence link here
    assert "<g class=\"node\" data-k=\"file\"" in out  # nothing to open: not a link


def test_conflict_edge_is_live_and_no_sources_still_draws():
    f = make(sources=[Source("a", "mit", True), Source("b", "cc-by-sa-4.0", True)], signal="conflict")
    out = str(svg(f))
    assert out.count('class="flow live" data-k="b"') == 2 and out.count("flow live") == 2
    empty = str(svg(make(id="o/" + "x" * 90, name="x" * 90)))
    assert empty.count('class="node') == 4 and "…" in empty  # decl and file in both diagrams; long id cut short
