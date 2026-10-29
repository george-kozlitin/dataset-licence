"""Licence id normalisation and signal rules."""

import re

PERMISSIVE = frozenset(
    {
        "mit", "apache-2.0", "bsd-2-clause", "bsd-3-clause", "cc0-1.0", "cc-by-4.0",
        "cc-by-3.0", "cc-by-2.0", "odc-by", "pddl", "unlicense", "isc",
    }
)
SHARE_ALIKE = frozenset(
    {
        "cc-by-sa-4.0", "cc-by-sa-3.0", "odbl", "gpl-2.0", "gpl-3.0", "lgpl-3.0",
        "agpl-3.0", "gfdl",
    }
)
_RESTRICTIVE_EXTRA = frozenset(
    {
        "openrail", "bigscience-openrail-m", "creativeml-openrail-m", "llama2", "llama3",
        "llama3.1", "llama3.2", "gemma", "c-uda", "cc-by-nd-4.0",
    }
)
RESTRICTIVE = frozenset(
    {
        "cc-by-nc-1.0", "cc-by-nc-2.0", "cc-by-nc-2.5", "cc-by-nc-3.0", "cc-by-nc-4.0",
        "cc-by-nc-sa-2.0", "cc-by-nc-sa-3.0", "cc-by-nc-sa-4.0",
        "cc-by-nc-nd-3.0", "cc-by-nc-nd-4.0",
    }
    | _RESTRICTIVE_EXTRA
)


def _one(item: object) -> str | None:
    if not isinstance(item, str):
        return None
    s = re.sub(r"[\s_]+", "-", item.strip().lower())
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or None


def normalise(raw: str | list[str] | None) -> str | None:
    """Canonical lower-case id; the first usable entry of a list wins. Never raises."""
    if isinstance(raw, (list, tuple)):
        for item in raw:
            s = _one(item)
            if s:
                return s
        return None
    return _one(raw)


def _is_restrictive(lic: str) -> bool:
    return lic in RESTRICTIVE or lic.startswith("cc-by-nc")


def _category(lic: str | None) -> str:
    if lic is None:
        return "unclear"
    if _is_restrictive(lic):
        return "restrictive"
    if lic in SHARE_ALIKE:
        return "share-alike"
    if lic in PERMISSIVE or lic.startswith("bsd-"):
        return "permissive"
    return "unclear"


def classify(
    stated: str | None, sources: list[str | None], file_licence: str | None
) -> tuple[str, str]:
    """Return (signal, reason); signal is permissive, restrictive, conflict or unclear."""
    tag = normalise(stated)
    cat = _category(tag)
    if cat == "unclear":
        return "unclear", "The checks could not identify a recognised licence ID in the dataset's metadata."

    file_lic = normalise(file_licence)
    file_cat = _category(file_lic)
    if file_cat != "unclear" and file_cat != cat:
        return "conflict", (
            f"The declared licence ({tag}) and the licence detected in a repository file "
            f"({file_lic}) fall into different categories, so the licences may conflict."
        )

    unknown = False
    for src in sources:
        s = normalise(src)
        scat = _category(s)
        if scat == "unclear":
            unknown = True
        elif scat == "restrictive" and cat != "restrictive":
            return "conflict", (
                f"The declared licence ({tag}) may conflict with a source dataset's reported "
                f"licence ({s}), which is classified as having use restrictions."
            )
        elif scat == "share-alike" and cat == "permissive":
            return "conflict", (
                f"The declared licence ({tag}) may conflict with a source dataset's reported "
                f"share-alike licence ({s})."
            )

    if unknown and cat != "restrictive":
        return "unclear", (
            "The checks could not identify a recognised licence ID "
            "for at least one declared source dataset."
        )
    if cat == "restrictive":
        return "restrictive", f"The declared licence ({tag}) is classified as having use restrictions."
    if cat == "share-alike":
        return "permissive", f"The declared licence ({tag}) is classified as share-alike."
    return "permissive", f"The declared licence ({tag}) is classified as permissive."
