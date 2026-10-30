"""Command line: python -m dlf build ..."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from dlf.facts import build_facts, refresh_facts
from dlf.hub import Hub, HubError
from dlf.render import STATIC_PAGES, render_site

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
    b.add_argument(
        "--cache", default="",
        help="directory of facts shards (data/f/*.json) from an earlier build; datasets whose "
        "Hub revision is unchanged are not fetched again",
    )
    b.add_argument("--static-pages", type=int, default=STATIC_PAGES, help="dataset pages written as files")
    b.add_argument(
        "--deadline-minutes", type=float, default=0,
        help="stop fetching after this long; later datasets use cached facts or wait for the next build",
    )
    return p


def load_cache(path: Path) -> dict[str, dict]:
    """Facts by lower-case ID from shard files; unreadable shards are skipped."""
    out: dict[str, dict] = {}
    for f in sorted(path.glob("*.json")) if path.is_dir() else []:
        try:
            shard = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print(f"warning: unreadable cache shard {f.name}", file=sys.stderr)
            continue
        if isinstance(shard, dict):
            out.update({k: v for k, v in shard.items() if isinstance(v, dict) and "id" in v})
    return out


def main(argv: list[str] | None = None, hub_factory=Hub, clock=time.monotonic) -> int:
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
    cache = load_cache(Path(args.cache)) if args.cache else {}
    known = {e["id"].lower(): e for e in entries if isinstance(e.get("id"), str)}
    deadline = clock() + args.deadline_minutes * 60 if args.deadline_minutes > 0 else None
    facts = []
    failed: list[tuple[str, str]] = []
    fetched = reused = stale = deferred = 0
    for i, entry in enumerate(entries, 1):
        cached = cache.get(str(entry.get("id")).lower())
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
            if cached is not None:  # keep last week's facts rather than drop the page
                try:
                    facts.append(refresh_facts(cached, entry, known))
                except Exception:  # noqa: BLE001, S110 - the failure is already counted
                    pass
        if i % PROGRESS_EVERY == 0:
            print(f"progress {i}/{total}", file=sys.stderr)
    print(f"fetched {fetched}, reused {reused}, stale {stale}, deferred {deferred}", file=sys.stderr)

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
    if over:
        print(f"error: {share:.1%} of datasets failed, above the {BUDGET:.0%} budget; nothing written",
              file=sys.stderr)  # fmt: skip
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
