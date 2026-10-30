import json
from typing import ClassVar

import pytest

import dlf.__main__ as cli
from dlf.facts import DatasetFacts
from dlf.hub import HubError


def fact(id_, signal="permissive"):
    owner, _, name = id_.partition("/")
    return DatasetFacts(
        id=id_, owner=owner, name=name, downloads=1, likes=0,
        last_modified="2026-09-01T10:00:00.000Z", stated_licence="mit",
        stated_from="tag", licence_name=None, licence_link=None,
        file_licence=None, signal=signal, reason="r",
    )  # fmt: skip


class FakeHub:
    listing: ClassVar[list[dict]] = []
    list_error = False

    def top_datasets(self, n):
        if self.list_error:
            raise HubError("listing down")
        return self.listing[:n]


built: list[str] = []


def setup(monkeypatch, n, failing=(), signals=None, list_error=False, unexpected=()):
    built.clear()
    FakeHub.listing = [{"id": f"o/d{i}", "sha": f"rev{i}", "downloads": 100 - i} for i in range(n)]
    FakeHub.list_error = list_error
    bad = {f"o/d{i}" for i in failing}
    odd = {f"o/d{i}" for i in unexpected}

    def fake_build(entry, hub, known=None):
        built.append(entry["id"])
        if entry["id"] in bad:
            raise HubError("boom " + entry["id"])
        if entry["id"] in odd:
            raise KeyError("weird")
        sig = (signals or {}).get(entry["id"], "permissive")
        return fact(entry["id"], sig)

    monkeypatch.setattr(cli, "build_facts", fake_build)


def run(tmp_path, *extra, clock=None):
    args = ["build", "--out", str(tmp_path / "site"), "--base-url", "https://x.test", *extra]
    if clock is None:
        return cli.main(args, hub_factory=FakeHub)
    return cli.main(args, hub_factory=FakeHub, clock=clock)


def write_cache(tmp_path, ids, rev=lambda i: f"rev{i}"):
    """A cache dir as an earlier build would leave it (one shard is enough for the loader)."""
    from dataclasses import asdict, replace

    d = tmp_path / "cache"
    d.mkdir(exist_ok=True)
    shard = {f"o/d{i}": asdict(replace(fact(f"o/d{i}"), revision=rev(i), reason="cached")) for i in ids}
    (d / "00.json").write_text(json.dumps(shard))
    (d / "01.json").write_text("not json")
    return str(d)


def test_three_of_hundred_failing_is_ok(monkeypatch, tmp_path, capsys):
    setup(monkeypatch, 100, failing=[1, 2, 3])
    assert run(tmp_path) == 0
    assert "skipped 3" in capsys.readouterr().out
    assert (tmp_path / "site" / "index.html").exists()


def test_six_of_hundred_failing_exits_2(monkeypatch, tmp_path, capsys):
    setup(monkeypatch, 100, failing=range(6))
    assert run(tmp_path) == 2
    assert "skipped 6" in capsys.readouterr().out


def test_empty_list_exits_2(monkeypatch, tmp_path):
    setup(monkeypatch, 0)
    assert run(tmp_path) == 2


def test_listing_error_exits_2(monkeypatch, tmp_path):
    setup(monkeypatch, 10, list_error=True)
    assert run(tmp_path) == 2


def test_summary_line_format(monkeypatch, tmp_path, capsys):
    setup(monkeypatch, 40, failing=[9], signals={"o/d0": "restrictive", "o/d1": "unclear", "o/d2": "restrictive"})
    assert run(tmp_path) == 0
    cap = capsys.readouterr()
    line = cap.out.strip().splitlines()[0]
    assert line.startswith("built ")
    assert line.endswith("skipped 1, signals {'permissive': 36, 'restrictive': 2, 'conflict': 0, 'unclear': 1}")
    assert "invalid ids 0" in cap.err
    assert "o/d9" in cap.err and "boom" in cap.err


def test_limit_and_progress(monkeypatch, tmp_path, capsys):
    setup(monkeypatch, 600)
    assert run(tmp_path, "--limit", "500") == 0
    err = capsys.readouterr().err
    assert "progress 250/500" in err and "progress 500/500" in err


def test_base_url_required(tmp_path):
    with pytest.raises(SystemExit) as e:
        cli.main(["build", "--out", str(tmp_path)], hub_factory=FakeHub)
    assert e.value.code == 2


def test_unexpected_exception_counts_and_is_reported(monkeypatch, tmp_path, capsys):
    setup(monkeypatch, 100, unexpected=[4])
    assert run(tmp_path) == 0
    cap = capsys.readouterr()
    assert "skipped 1" in cap.out
    assert "o/d4: KeyError" in cap.err


def test_unexpected_exceptions_share_the_budget(monkeypatch, tmp_path):
    setup(monkeypatch, 100, failing=[1, 2, 3], unexpected=[4, 5, 6])
    assert run(tmp_path) == 2


def test_unchanged_revision_is_not_fetched(monkeypatch, tmp_path, capsys):
    setup(monkeypatch, 4)
    cache = write_cache(tmp_path, [0, 1, 2], rev=lambda i: "old" if i == 2 else f"rev{i}")
    assert run(tmp_path, "--cache", cache) == 0
    assert built == ["o/d2", "o/d3"]  # d2 changed, d3 is new
    err = capsys.readouterr().err
    assert "fetched 2, reused 2, stale 0, deferred 0" in err
    assert "unreadable cache shard 01.json" in err
    assert (tmp_path / "site/d/o/d0/index.html").is_file()


def test_deadline_uses_stale_facts_and_defers_the_rest(monkeypatch, tmp_path, capsys):
    setup(monkeypatch, 4, failing=[3])
    cache = write_cache(tmp_path, [1], rev=lambda i: "old")
    ticks = iter([0, 0] + [10_000] * 50)  # start, first dataset in time, then past the deadline
    assert run(tmp_path, "--cache", cache, "--deadline-minutes", "1", clock=lambda: next(ticks)) == 0
    assert built == ["o/d0"]
    out, err = capsys.readouterr()
    assert "fetched 1, reused 0, stale 1, deferred 2" in err
    assert "skipped 0" in out  # o/d3 would fail, but it was never tried
    assert (tmp_path / "site/d/o/d1/index.html").is_file()
    assert not (tmp_path / "site/d/o/d2").exists()


def test_failed_fetch_keeps_cached_facts(monkeypatch, tmp_path, capsys):
    setup(monkeypatch, 3, failing=[1])
    cache = write_cache(tmp_path, [1], rev=lambda i: "old")
    assert run(tmp_path, "--cache", cache) == 2  # 1 of 3 failed: over the budget, nothing written
    setup(monkeypatch, 100, failing=[1])
    assert run(tmp_path, "--cache", cache) == 0
    assert (tmp_path / "site/d/o/d1/index.html").is_file()
