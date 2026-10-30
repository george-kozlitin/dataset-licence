"""Cases that pin the Worker's live check (mcp/src/check.ts) to the Python reference.

Run `python -m tests.parity` to rewrite tests/fixtures/parity.json after a rule change;
tests/test_parity.py fails while the file is out of date, and the Worker's tests read it.
"""

from __future__ import annotations

import json
from pathlib import Path

from dlf import facts, shards
from dlf.licences import _category, classify, normalise

FIXTURE = Path(__file__).parent / "fixtures" / "parity.json"
CARDS = Path(__file__).parent / "fixtures" / "cards"

NORMALISE = [
    "MIT", " Apache_2.0 ", "cc--by--4.0", "-x-", "", None, 3, ["", "CC-BY-SA-4.0"], ["", None], "llama 3.1",
]
LICENCES = [
    "mit", "apache-2.0", "cc-by-sa-4.0", "cc-by-nc-4.0", "cc-by-nc-sa-2.5", "bsd-4-clause", "openrail",
    "gpl-3.0", "other", "unknown", None,
]
CLASSIFY = [
    ("mit", [], None),
    ("apache-2.0", ["cc-by-sa-4.0"], None),
    ("apache-2.0", ["cc-by-nc-4.0", "cc-by-sa-4.0"], None),
    ("cc-by-sa-4.0", ["cc-by-nc-4.0"], None),
    ("cc-by-nc-4.0", ["cc-by-sa-4.0", None], None),
    ("mit", [None], None),
    ("mit", [], "cc-by-nc-4.0"),
    ("mit", [], "apache-2.0"),
    ("cc-by-sa-4.0", [], "mit"),
    ("other", ["mit"], None),
    (None, [], None),
    ("Apache 2.0", ["MIT"], "unrecognised"),
]
LICENCE_TEXTS = [
    "SPDX-License-Identifier: Apache-2.0\n",
    "                                 Apache License\n                           Version 2.0, January 2004",
    "Creative Commons Zero v1.0 Universal",
    "Attribution-NonCommercial-ShareAlike 4.0 International",
    "Attribution-NoDerivs 3.0 Unported",
    "This work is licensed under CC BY-SA 4.0",
    "licensed under cc-by-nc 4.0",
    "GNU AFFERO GENERAL PUBLIC LICENSE Version 3",
    "GNU LESSER GENERAL PUBLIC LICENSE",
    "GNU GENERAL PUBLIC LICENSE\n Version 2, June 1991",
    "GNU GENERAL PUBLIC LICENSE",
    "Redistribution and use in source and binary forms ... Neither the name of",
    "Redistribution and use in source and binary forms",
    "  MIT License\n\nCopyright",
    "Permission is hereby granted, free of charge, to any person",
    "Custom terms. All rights reserved.",
    "",
    None,
    "x" * 2100 + "MIT License",
]
CUSTOM_NC = [
    ("other", "Research only licence", ""),
    (None, None, "# License\nFor non-commercial use.\n# Other\nnon-commercial"),
    ("unknown", None, "# Licensing\nThis is not for commercial use."),
    ("other", None, "# License\nThere is no non-commercial restriction."),
    ("other", None, "# License\nNot non-commercial, but also research only."),
    ("mit", "non-commercial", ""),
    ("other", None, "# About\nnon-commercial\n```\n# License\n```\nresearch only"),
]
SOURCE_IDS = [
    ({"source_datasets": ["original", "extended|squad", "Extended| glue ", "https://x.y/z", "me/self", "squad", "a/b"]}, "me/self"),
    ({"source_datasets": "single/source"}, "me/x"),
    ({"source_datasets": [1, None, "ok"]}, "me/x"),
    ({}, "me/x"),
]
ENTRY_LICENCE = [
    ({"tags": ["license:MIT", "license:apache-2.0"]}, {"license": "gpl-3.0"}),
    ({"tags": ["task:x", "license:"]}, {"license": ["", "CC-BY-4.0"]}),
    ({"tags": "license:mit"}, {}),
    ({}, {}),
]
CARD_FILES = ["bibtex.md", "custom_nc.md", "long_citation.md", "malformed.md", "no_yaml.md"]
VALID_IDS = ["a/b", "squad", "a/b/c", "../x", "a/..", "a b/c", "a.b-c_d/E.F", ""]


def cases() -> dict:
    cards = {name: (CARDS / name).read_text(encoding="utf-8") for name in CARD_FILES}
    return {
        "normalise": [[v, normalise(v)] for v in NORMALISE],
        "category": [[v, _category(v)] for v in LICENCES],
        "classify": [[list(args), list(classify(*args))] for args in CLASSIFY],
        "detect_licence_text": [[t, facts.detect_licence_text(t)] for t in LICENCE_TEXTS],
        "custom_nc": [[list(args), facts._custom_nc(*args)] for args in CUSTOM_NC],
        "source_ids": [[list(args), facts._source_ids(*args)] for args in SOURCE_IDS],
        "entry_licence": [[list(args), list(facts._entry_licence(*args))] for args in ENTRY_LICENCE],
        "valid_id": [[v, facts._valid_source_id(v)] for v in VALID_IDS],
        "shards": [
            [v, shards.fnv1a(v), shards.facts_shard(v), shards.page_shard(v)]
            for v in ["google/xtreme", "Google/XTREME", "squad", "a", "", "allenai/tulu-3-sft-mixture"]
        ],
        "cards": [
            {
                "name": name,
                "text": text,
                "body": facts._body(text),
                "citation": facts._citation(facts._body(text)),
            }
            for name, text in cards.items()
        ],
    }


def render() -> str:
    return json.dumps(cases(), ensure_ascii=False, indent=1) + "\n"


if __name__ == "__main__":
    FIXTURE.write_text(render(), encoding="utf-8")
