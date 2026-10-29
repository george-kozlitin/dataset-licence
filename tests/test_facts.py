from pathlib import Path

import pytest

from dlf.facts import DatasetFacts, Source, build_facts, detect_licence_text
from dlf.hub import HubError

CARDS = Path(__file__).parent / "fixtures" / "cards"


def card(name):
    return (CARDS / name).read_text()


class FakeHub:
    def __init__(self, datasets=None, cards=None, files=None, raws=None, errors=()):
        self.datasets = datasets or {}
        self.cards = cards or {}
        self.file_map = files or {}
        self.raws = raws or {}
        self.errors = set(errors)
        self.calls = []
        self.tree_calls = []

    def dataset(self, id):
        self.calls.append(("dataset", id))
        if id in self.errors:
            raise HubError("boom")
        return self.datasets.get(id)

    def files(self, id):
        self.tree_calls.append(id)
        return self.file_map.get(id)

    def raw(self, id, path, max_bytes=2048):
        return self.raws.get((id, path))

    def card(self, id):
        return self.cards.get(id)


def entry(id="acme/data", tags=None, card_data=None, **kw):
    e = {
        "id": id,
        "downloads": 10,
        "likes": 2,
        "lastModified": "2026-01-02T03:04:05.000Z",
        "tags": tags if tags is not None else [],
        "gated": False,
    }
    if card_data is not None:
        e["cardData"] = card_data
    e.update(kw)
    return e


def test_permissive_no_sources():
    hub = FakeHub()
    f = build_facts(entry(tags=["license:mit"], card_data={"task_categories": ["translation"]}), hub)
    assert isinstance(f, DatasetFacts)
    assert (f.owner, f.name) == ("acme", "data")
    assert f.stated_licence == "mit" and f.stated_from == "tag"
    assert f.signal == "permissive"
    assert f.sources == [] and f.tasks == ["translation"]
    assert (f.downloads, f.likes, f.last_modified) == (10, 2, "2026-01-02T03:04:05.000Z")
    assert f.gated is False


def test_ownerless_id():
    f = build_facts(entry(id="squad", tags=["license:mit"]), FakeHub())
    assert f.owner is None and f.name == "squad"


def test_stated_from_card_and_none():
    f = build_facts(entry(card_data={"license": ["CC-BY-4.0"]}), FakeHub())
    assert (f.stated_licence, f.stated_from) == ("cc-by-4.0", "card")
    f = build_facts(entry(), FakeHub())
    assert (f.stated_licence, f.stated_from, f.signal) == (None, "none", "unclear")


def test_cc_by_with_nc_source_is_conflict():
    hub = FakeHub(datasets={"other/src": entry("other/src", tags=["license:cc-by-nc-4.0"])})
    f = build_facts(
        entry(tags=["license:cc-by-4.0"], card_data={"source_datasets": ["other/src"]}), hub
    )
    assert f.signal == "conflict"
    assert f.sources == [Source("other/src", "cc-by-nc-4.0", True)]


def test_missing_source_is_not_found_and_unclear():
    f = build_facts(
        entry(tags=["license:mit"], card_data={"source_datasets": "ghost/gone"}), FakeHub()
    )
    assert f.sources == [Source("ghost/gone", None, False)]
    assert f.signal == "unclear"


def test_source_hub_error_propagates():
    hub = FakeHub(errors={"bad/src"})
    with pytest.raises(HubError):
        build_facts(entry(tags=["license:mit"], card_data={"source_datasets": ["bad/src"]}), hub)


def test_ignored_sources_and_self_reference():
    hub = FakeHub()
    f = build_facts(
        entry(
            tags=["license:mit"],
            card_data={
                "source_datasets": [
                    "original",
                    "extended",
                    "https://example.com/x",
                    "acme/data",
                    "ACME/Data",
                ]
            },
        ),
        hub,
    )
    assert f.sources == []
    assert hub.calls == []
    assert f.signal == "permissive"


def test_extended_pipe_form_fetches_named_source():
    hub = FakeHub(datasets={"foo/bar": entry("foo/bar", tags=["license:mit"])})
    f = build_facts(entry(tags=["license:mit"], card_data={"source_datasets": "extended|foo/bar"}), hub)
    assert [s.id for s in f.sources] == ["foo/bar"]


def test_card_without_yaml_uses_card_data_and_citation_section():
    hub = FakeHub(cards={"acme/data": card("no_yaml.md")})
    f = build_facts(entry(card_data={"license": "mit"}), hub)
    assert f.stated_licence == "mit"
    assert f.citation == "Please cite the authors of Plain Dataset (2024)."


def test_malformed_yaml_does_not_raise_and_falls_back():
    hub = FakeHub(cards={"acme/data": card("malformed.md")})
    f = build_facts(entry(card_data={"license": "apache-2.0", "task_categories": ["x"]}), hub)
    assert f.stated_licence == "apache-2.0"
    assert f.tasks == ["x"]
    assert f.citation is None


def test_card_front_matter_is_read():
    hub = FakeHub(cards={"acme/data": card("bibtex.md")})
    f = build_facts(entry(), hub)
    assert f.stated_licence == "mit" and f.stated_from == "card"


def test_long_citation_capped():
    hub = FakeHub(cards={"acme/data": card("long_citation.md")})
    f = build_facts(entry(), hub)
    assert f.citation is not None and len(f.citation) == 1500


def test_bibtex_extraction():
    hub = FakeHub(cards={"acme/data": card("bibtex.md")})
    f = build_facts(entry(), hub)
    assert f.citation.startswith("@article{foo2024,")
    assert f.citation.rstrip().endswith("}")
    assert "Some words first" not in f.citation
    assert "```" not in f.citation


def test_citation_is_raw_text_not_escaped():
    # escaping happens at render time (autoescape); the data stays raw
    hub = FakeHub(cards={"acme/data": card("bibtex.md")})
    assert "<b>bar</b>" in build_facts(entry(), hub).citation


def test_license_file_conflict():
    hub = FakeHub(
        files={"acme/data": ["README.md", "LICENSE.txt", "data.csv"]},
        raws={
            ("acme/data", "LICENSE.txt"): "Creative Commons\nAttribution-NonCommercial 4.0 International\n"
        },
    )
    f = build_facts(entry(tags=["license:mit"]), hub)
    assert f.file_licence == "cc-by-nc-4.0"
    assert f.licence_files == ["LICENSE.txt"]
    assert f.signal == "conflict"


def test_license_file_case_insensitive_and_unknown_text():
    hub = FakeHub(
        files={"acme/data": ["licence.md", "Copying"]},
        raws={("acme/data", "licence.md"): "All rights reserved by nobody in particular."},
    )
    f = build_facts(entry(tags=["license:mit"]), hub)
    assert f.licence_files == ["licence.md", "Copying"]
    assert f.file_licence is None
    assert f.signal == "permissive"


def test_no_files_listing():
    f = build_facts(entry(tags=["license:mit"]), FakeHub())
    assert f.licence_files == [] and f.file_licence is None


@pytest.mark.parametrize("value", ["manual", "auto", True, False])
def test_gated_kept_as_is(value):
    f = build_facts(entry(tags=["license:mit"], gated=value), FakeHub())
    assert f.gated == value


def test_gated_missing_is_false():
    e = entry(tags=["license:mit"])
    del e["gated"]
    assert build_facts(e, FakeHub()).gated is False


def test_custom_noncommercial_licence_from_card_section():
    hub = FakeHub(cards={"acme/data": card("custom_nc.md")})
    f = build_facts(entry(), hub)
    assert f.stated_licence == "other"
    assert f.signal == "restrictive"
    assert f.reason == (
        "the card's licence text appears to restrict use to non-commercial or research purposes"
    )


def test_custom_noncommercial_from_licence_name_with_none_stated():
    f = build_facts(entry(card_data={"license_name": "Research Purposes Only v1"}), FakeHub())
    assert f.stated_licence is None
    assert f.signal == "restrictive"


def test_other_without_nc_text_stays_unclear():
    f = build_facts(entry(tags=["license:other"], card_data={"license_name": "acme-v1"}), FakeHub())
    assert f.signal == "unclear"


def test_nc_text_ignored_when_licence_is_known():
    f = build_facts(
        entry(tags=["license:mit"], card_data={"license_name": "non-commercial"}), FakeHub()
    )
    assert f.signal == "permissive"


def test_licence_name_and_link():
    f = build_facts(
        entry(card_data={"license": "other", "license_name": "n", "license_link": "https://x/y"}),
        FakeHub(),
    )
    assert (f.licence_name, f.licence_link) == ("n", "https://x/y")


def test_card_fetch_failure_text_none_is_fine():
    assert build_facts(entry(tags=["license:mit"]), FakeHub()).citation is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("MIT License\n\nCopyright (c) 2024", "mit"),
        ("Permission is hereby granted, free of charge, to any person", "mit"),
        ("Apache License\nVersion 2.0, January 2004", "apache-2.0"),
        ("SPDX-License-Identifier: BSD-3-Clause", "bsd-3-clause"),
        ("Redistribution and use in source and binary forms ... Neither the name of", "bsd-3-clause"),
        ("Redistribution and use in source and binary forms, with or without", "bsd-2-clause"),
        ("CC0 1.0 Universal", "cc0-1.0"),
        ("Attribution 4.0 International", "cc-by-4.0"),
        ("Attribution-ShareAlike 4.0 International", "cc-by-sa-4.0"),
        ("Attribution-NonCommercial-ShareAlike 4.0", "cc-by-nc-sa-4.0"),
        ("Attribution-NonCommercial-NoDerivatives 4.0", "cc-by-nc-nd-4.0"),
        ("licensed under CC BY-NC 4.0", "cc-by-nc-4.0"),
        ("GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007", "gpl-3.0"),
        ("GNU GENERAL PUBLIC LICENSE\nVersion 2, June 1991", "gpl-2.0"),
        ("some bespoke terms", None),
        ("", None),
    ],
)
def test_detect_licence_text(text, expected):
    assert detect_licence_text(text) == expected


def test_non_string_file_entries_ignored():
    hub = FakeHub(files={"acme/data": [None, 5, "LICENSE"]})
    assert build_facts(entry(tags=["license:mit"]), hub).licence_files == ["LICENSE"]


def test_non_numeric_counts_become_zero():
    f = build_facts(entry(tags=["license:mit"], downloads="many", likes=None), FakeHub())
    assert (f.downloads, f.likes) == (0, 0)
    assert build_facts(entry(downloads="12", likes=3.0), FakeHub()).downloads == 12


@pytest.mark.parametrize(
    "name", ["Non Commercial v2", "Not for commercial use", "Academic use only"]
)
def test_new_nc_phrases(name):
    f = build_facts(entry(card_data={"license": "other", "license_name": name}), FakeHub())
    assert f.signal == "restrictive"


@pytest.mark.parametrize(
    "name", ["This is NOT a non-commercial licence", "no non commercial restriction"]
)
def test_negated_nc_phrase_skipped(name):
    f = build_facts(entry(card_data={"license": "other", "license_name": name}), FakeHub())
    assert f.signal == "unclear"


def test_siblings_used_without_tree_call():
    hub = FakeHub(raws={("acme/data", "LICENSE"): "MIT License\n"})
    e = entry(tags=["license:mit"])
    e["siblings"] = [{"rfilename": "LICENSE"}, {"rfilename": "data/a.csv"}]
    f = build_facts(e, hub)
    assert f.licence_files == ["LICENSE"] and f.file_licence == "mit"
    assert hub.tree_calls == []


def test_tree_fallback_when_siblings_absent():
    hub = FakeHub(files={"acme/data": ["LICENSE"]})
    assert build_facts(entry(tags=["license:mit"]), hub).licence_files == ["LICENSE"]


@pytest.mark.parametrize("bad", ["Wikipedia dumps", "a?b", "x/../../models/y"])
def test_invalid_source_id_not_fetched(bad):
    hub = FakeHub()
    f = build_facts(entry(card_data={"source_datasets": [bad]}), hub)
    assert f.sources == [Source(bad, None, False)]
    assert not [c for c in hub.calls if c[0] == "dataset"]
