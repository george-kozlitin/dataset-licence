"""Command line: python -m dlf build ..."""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from dlf import shards
from dlf.facts import build_facts, refresh_facts, slim_entry
from dlf.hub import Hub, HubError
from dlf.render import STATIC_PAGES, render_site

SIGNALS = ("permissive", "restrictive", "conflict", "unclear")
BUDGET = 0.05
PROGRESS_EVERY = 250


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dlf")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build the static site")
    b.add_argument("--limit", type=int, default=5000, help="datasets to cover, most downloaded first; 0 = all")
    b.add_argument("--out", default="site")
    b.add_argument("--base-url", required=True)
    b.add_argument("--contact", default="")
    b.add_argument(
        "--cache", default="",
        help="directory of facts shards (*.txt, or the earlier *.json) from an earlier build; datasets "
        "whose Hub revision is unchanged are not fetched again",
    )
    b.add_argument(
        "--cache-out", default="",
        help="write the facts of every listed dataset here as shards right after fetching, before "
        "rendering; may be the --cache directory",
    )
    b.add_argument("--static-pages", type=int, default=STATIC_PAGES, help="dataset pages written as files")
    b.add_argument(
        "--deadline-minutes", type=float, default=0,
        help="stop fetching this long after start (listing included); later datasets use cached "
        "facts or wait for the next build",
    )
    return p


def _report(fetched: int, reused: int, stale: int, deferred: int, total: int) -> None:
    """The machine-readable progress line, and the step outputs the workflow chains on."""
    print(f"progress fetched={fetched} reused={reused} stale={stale} deferred={deferred} total={total}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"deferred={deferred}\nfetched={fetched}\n")


def main(argv: list[str] | None = None, hub_factory=Hub, clock=time.monotonic) -> int:
    args = _parser().parse_args(argv)
    started = clock()  # the deadline counts from here, so listing time is included
    deadline = started + args.deadline_minutes * 60 if args.deadline_minutes > 0 else None
    cache = shards.read_facts(Path(args.cache)) if args.cache else {}

    def shape(entry: dict) -> dict:
        cached = cache.get(str(entry.get("id")).lower())
        same = cached is not None and cached.get("revision") and cached.get("revision") == entry.get("sha")
        return slim_entry(entry, fetch=not same)

    hub = hub_factory()
    try:
        entries = hub.top_datasets(args.limit, shape=shape)
    except HubError as e:
        print(f"error: cannot list datasets: {e}", file=sys.stderr)
        return 2
    if not entries:
        print("error: the top list is empty", file=sys.stderr)
        return 2

    total = len(entries)
    known = {e["id"].lower(): e for e in entries if isinstance(e.get("id"), str)}
    facts = []
    failed: list[tuple[str, str]] = []
    fetched = reused = stale = deferred = 0
    for i, entry in enumerate(entries, 1):
        # pop: facts of datasets gone from the Hub are not carried over, and memory is freed as we go
        cached = cache.pop(str(entry.get("id")).lower(), None)
        same = cached is not None and cached.get("revision") and cached.get("revision") == entry.get("sha")
        try:
            if same:
                facts.append(refresh_facts(cached, entry, known))
                reused += 1
            elif deadline is not None and clock() > deadline:
                if cached is not None:
                    facts.append(refresh_facts(cached, entry, known))
                    stale += 1
                else:
                    deferred += 1
            else:
                facts.append(build_facts(entry, hub, known))
                fetched += 1
        except Exception as e:  # noqa: BLE001 - one bad dataset must not stop the build
            failed.append((str(entry.get("id")), f"{type(e).__name__}: {e}"))
            if cached is not None:  # keep the last build's facts rather than drop the page
                try:
                    facts.append(refresh_facts(cached, entry, known))
                except Exception:  # noqa: BLE001, S110 - the failure is already counted
                    pass
        if i % PROGRESS_EVERY == 0:
            print(f"progress {i}/{total}", file=sys.stderr)
    cache.clear()
    print(f"fetched {fetched}, reused {reused}, stale {stale}, deferred {deferred}", file=sys.stderr)

    # Saved before rendering and the budget check, so hours of fetching survive either failing.
    cache_error = False
    if args.cache_out:
        try:
            n = shards.write_facts((asdict(f) for f in facts), Path(args.cache_out))
            print(f"cache {len(facts)} facts in {n} shards", file=sys.stderr)
        except OSError as e:
            print(f"error: cannot write the facts cache: {e}", file=sys.stderr)
            cache_error = True

    # Datasets left for the next build (deadline) are not failures.
    share = len(failed) / max(1, total - deferred)
    over = share > BUDGET
    stats = None
    if not over:
        built_at = datetime.now(UTC).strftime("%Y-%m-%d")
        try:
            stats = render_site(
                facts, Path(args.out), args.base_url, built_at, contact=args.contact,
                static_pages=args.static_pages,
            )
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            _report(fetched, reused, stale, deferred, total)
            return 2

    counts = Counter(f.signal for f in facts)
    signals = {s: counts.get(s, 0) for s in SIGNALS}
    pages = stats.pages if stats else 0
    print(f"built {pages} pages, skipped {len(failed)}, signals {signals}")
    if stats:
        print(f"static {stats.static_pages}, noindex {stats.noindex}, files {stats.files}", file=sys.stderr)
    print(f"invalid ids {stats.skipped_ids if stats else 0}", file=sys.stderr)
    for id_, err in failed[:5]:
        print(f"skipped {id_}: {err}", file=sys.stderr)
    _report(fetched, reused, stale, deferred, total)
    if over:
        print(f"error: {share:.1%} of datasets failed, above the {BUDGET:.0%} budget; nothing written",
              file=sys.stderr)  # fmt: skip
        return 2
    return 2 if cache_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
