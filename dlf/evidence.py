"""Evidence links: the Hub file or record each licence value was read from, pinned to a revision."""

from __future__ import annotations

from urllib.parse import quote

HF = "https://huggingface.co"


def _repo(id: str) -> str:
    return "/".join(quote(p, safe="") for p in id.split("/"))


def _rev(rev: str | None) -> str:
    return quote(rev, safe="") if rev else "main"


def card_url(id: str, rev: str | None) -> str:
    """The dataset card; the Hub derives the licence tag from its YAML header."""
    return file_url(id, rev, "README.md")


def file_url(id: str, rev: str | None, path: str) -> str:
    return f"{HF}/datasets/{_repo(id)}/blob/{_rev(rev)}/{quote(path)}"


def tree_url(id: str, rev: str | None) -> str:
    """The top-level file list, for 'nothing found here' facts."""
    return f"{HF}/datasets/{_repo(id)}/tree/{_rev(rev)}"


def api_url(id: str, rev: str | None) -> str:
    """The Hub API record (tags and card metadata), when the repository has no card."""
    return f"{HF}/api/datasets/{_repo(id)}" + (f"/revision/{_rev(rev)}" if rev else "")


def page_url(id: str) -> str:
    return f"{HF}/datasets/{_repo(id)}"


def label(url: str | None) -> str:
    """Short link text: 'README.md at ec5f1f4', 'file list at ec5f1f4', 'Hub API record', 'Hub page'."""
    if not url:
        return ""
    path = url.removeprefix(HF)
    rev = ""
    for marker in ("/blob/", "/tree/", "/revision/"):
        if marker in path:
            rest = path.split(marker, 1)[1]
            rev = rest.split("/", 1)[0]
            break
    short = f" at {rev[:7]}" if rev and rev != "main" else ""
    if "/blob/" in path:
        return path.rsplit("/", 1)[1] + short
    if "/tree/" in path:
        return "file list" + short
    if path.startswith("/api/"):
        return "Hub API record" + short
    return "Hub page"
