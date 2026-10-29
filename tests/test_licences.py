import pytest

from dlf.licences import PERMISSIVE, RESTRICTIVE, SHARE_ALIKE, classify, normalise


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("CC-BY-4.0", "cc-by-4.0"),
        ("cc_by_4.0", "cc-by-4.0"),
        ("Apache 2.0", "apache-2.0"),
        (["mit"], "mit"),
        (["mit", "apache-2.0"], "mit"),
        (None, None),
        ("", None),
        ([], None),
        ("other", "other"),
        ("unknown", "unknown"),
        ([None, "mit"], "mit"),
        ([5], None),
        (5, None),
    ],
)
def test_normalise(raw, expected):
    assert normalise(raw) == expected


def test_sets_are_frozensets():
    for s in (PERMISSIVE, SHARE_ALIKE, RESTRICTIVE):
        assert isinstance(s, frozenset)
    assert "cc-by-nc-sa-4.0" in RESTRICTIVE
    assert "cc-by-nd-4.0" in RESTRICTIVE
    assert not (PERMISSIVE & SHARE_ALIKE)


def test_permissive():
    assert classify("mit", [], None)[0] == "permissive"


def test_share_alike_is_permissive_with_reason():
    signal, reason = classify("cc-by-sa-4.0", [], None)
    assert signal == "permissive"
    assert "share-alike" in reason


def test_restrictive():
    assert classify("cc-by-nc-4.0", [], None)[0] == "restrictive"


def test_unclear():
    assert classify(None, [], None)[0] == "unclear"
    assert classify("other", [], None)[0] == "unclear"
    assert classify("some-new-licence", [], None)[0] == "unclear"


def test_restrictive_source_conflicts():
    assert classify("cc-by-4.0", ["cc-by-nc-4.0"], None)[0] == "conflict"


def test_share_alike_source_under_permissive_conflicts():
    assert classify("mit", ["cc-by-sa-3.0"], None)[0] == "conflict"


def test_unknown_source():
    signal, reason = classify("mit", [None], None)
    assert signal == "unclear"
    assert "source licence unknown" in reason


def test_file_licence_disagreement():
    assert classify("mit", [], "cc-by-nc-4.0")[0] == "conflict"


def test_file_licence_agreement():
    assert classify("mit", [], "apache-2.0")[0] == "permissive"


def test_signal_values_are_closed_set():
    for args in [("mit", [], None), (None, [], None), ("mit", ["x"], None)]:
        assert classify(*args)[0] in {"permissive", "restrictive", "conflict", "unclear"}


def test_bsd_prefix_is_permissive():
    assert classify("bsd-3-clause-clear", [], None)[0] == "permissive"


def test_restrictive_tag_stays_restrictive_with_unknown_source():
    assert classify("cc-by-nc-4.0", [None], None)[0] == "restrictive"


def test_restrictive_tag_restrictive_source():
    assert classify("cc-by-nc-4.0", ["cc-by-nc-sa-4.0"], None)[0] == "restrictive"


def test_restrictive_tag_permissive_file_conflicts():
    assert classify("cc-by-nc-4.0", [], "mit")[0] == "conflict"


def test_share_alike_tag_share_alike_source():
    signal, reason = classify("cc-by-sa-4.0", ["cc-by-sa-3.0"], None)
    assert signal == "permissive"
    assert "share-alike" in reason
