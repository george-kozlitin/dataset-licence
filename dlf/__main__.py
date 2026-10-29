"""Command line: python -m dlf build ..."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from dlf.facts import build_facts
from dlf.hub import Hub, HubError
from dlf.render import render_site

SIGNALS = ("permissive", "restrictive", "conflict", "unclear")
BUDGET = 0.05
PROGRESS_EVERY = 250


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dlf")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build the static site")
    b.add_argument("--limit", type=int, default=5000)
    b.add_argument("--out", default="site")
    b.add_argument("--base-url", required=True)
    b.add_argument("--contact", default="")
    return p


def main(argv: list[str] | None = None, hub_factory=Hub) -> int:
    args = _parser().parse_args(argv)
    hub = hub_factory()
    try:
        entries = hub.top_datasets(args.limit)
    except HubError as e:
        print(f"error: cannot list datasets: {e}", file=sys.stderr)
        return 2
    if not entries:
        print("error: the top list is empty", file=sys.stderr)
        return 2

    total = len(entries)
    facts = []
    failed: list[tuple[str, str]] = []
    for i, entry in enumerate(entries, 1):
        try:
            facts.append(build_facts(entry, hub))
        except Exception as e:  # noqa: BLE001 - one bad dataset must not stop the build
            failed.append((str(entry.get("id")), f"{type(e).__name__}: {e}"))
        if i % PROGRESS_EVERY == 0:
            print(f"progress {i}/{total}", file=sys.stderr)

    share = len(failed) / total
    over = share > BUDGET
    stats = None
    if not over:
        built_at = datetime.now(UTC).strftime("%Y-%m-%d")
        stats = render_site(facts, Path(args.out), args.base_url, built_at, contact=args.contact)

    counts = Counter(f.signal for f in facts)
    signals = {s: counts.get(s, 0) for s in SIGNALS}
    pages = stats.pages if stats else 0
    print(f"built {pages} pages, skipped {len(failed)}, signals {signals}")
    print(f"invalid ids {stats.skipped_ids if stats else 0}", file=sys.stderr)
    for id_, err in failed[:5]:
        print(f"skipped {id_}: {err}", file=sys.stderr)
    if over:
        print(f"error: {share:.1%} of datasets failed, above the {BUDGET:.0%} budget; nothing written",
              file=sys.stderr)  # fmt: skip
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
