import json
from typing import ClassVar

import pytest

import dlf.__main__ as cli
from dlf import shards
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
    shaped: ClassVar[list[dict]] = []
    on_list = None  # called once per listing, e.g. to advance a fake clock

    def top_datasets(self, n, shape=None):
        if self.list_error:
            raise HubError("listing down")
        if FakeHub.on_list:
            FakeHub.on_list()
        got = self.listing if n <= 0 else self.listing[:n]
        FakeHub.shaped = [shape(e) for e in got] if shape else list(got)
        return FakeHub.shaped


built: list[str] = []


@pytest.fixture(autouse=True)
def _no_github_output(monkeypatch):
    """CI sets GITHUB_OUTPUT for the test step itself; builds under test must not write to it."""
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)


def setup(monkeypatch, n, failing=(), signals=None, list_error=False, unexpected=()):
    built.clear()
    FakeHub.listing = [{"id": f"o/d{i}", "sha": f"rev{i}", "downloads": 100 - i} for i in range(n)]
    FakeHub.list_error = list_error
    FakeHub.on_list = None
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


def test_limit_zero_covers_every_listed_dataset(monkeypatch, tmp_path, capsys):
    setup(monkeypatch, 7)
    assert run(tmp_path, "--limit", "0") == 0
    assert len(built) == 7
    assert "progress fetched=7 reused=0 stale=0 deferred=0 total=7" in capsys.readouterr().out


def test_reused_entries_are_slimmed_fetched_keep_card_fields(monkeypatch, tmp_path):
    setup(monkeypatch, 2)
    heavy = {
        "description": "x" * 1000,
        "citation": "@misc{...}",
        "tags": ["license:mit", "task_categories:translation", "size_categories:1K<n<10K"],
        "cardData": {"license": "mit", "license_name": "n", "task_categories": ["t"], "dataset_info": {"f": 1}},
        "siblings": [{"rfilename": "README.md"}, {"rfilename": "LICENSE"}, {"rfilename": "data/a.parquet"}],
    }
    for e in FakeHub.listing:
        e.update(heavy)
    cache = write_cache(tmp_path, [0])  # d0 unchanged, d1 new
    assert run(tmp_path, "--cache", cache) == 0
    reused, fetched = FakeHub.shaped
    for e in (reused, fetched):
        assert "description" not in e and "citation" not in e
        assert e["tags"] == ["license:mit"]
        assert "dataset_info" not in e["cardData"]
    assert reused["cardData"] == {"license": "mit"}
    assert reused["siblings"] == [{"rfilename": "README.md"}]
    assert fetched["cardData"] == {"license": "mit", "license_name": "n", "task_categories": ["t"]}
    assert fetched["siblings"] == [{"rfilename": "README.md"}, {"rfilename": "LICENSE"}]
    assert built == ["o/d1"]


def test_deadline_counts_listing_time(monkeypatch, tmp_path, capsys):
    setup(monkeypatch, 3)
    cache = write_cache(tmp_path, [0])
    now = [0.0]
    FakeHub.on_list = lambda: now.__setitem__(0, 61.0)  # listing alone takes past the 1 minute deadline
    assert run(tmp_path, "--cache", cache, "--deadline-minutes", "1", clock=lambda: now[0]) == 0
    assert built == []
    assert "progress fetched=0 reused=1 stale=0 deferred=2 total=3" in capsys.readouterr().out


def test_cache_out_written_when_budget_fails(monkeypatch, tmp_path):
    setup(monkeypatch, 3, failing=[1])
    cache = write_cache(tmp_path, [1, 2], rev=lambda i: "old" if i == 1 else f"rev{i}")
    out = tmp_path / "out"
    assert run(tmp_path, "--cache", cache, "--cache-out", str(out)) == 2  # 1 of 3 failed
    got = shards.read_facts(out)
    assert sorted(got) == ["o/d0", "o/d1", "o/d2"]  # fetched, kept on failure, reused
    assert got["o/d1"]["revision"] == "old"
    assert not list(out.glob("*.json"))
    assert not (tmp_path / "site" / "index.html").exists()


def test_cache_out_written_when_render_raises(monkeypatch, tmp_path):
    setup(monkeypatch, 3)

    def boom(*a, **k):
        raise ValueError("render broke")

    monkeypatch.setattr(cli, "render_site", boom)
    out = tmp_path / "out"
    with pytest.raises(ValueError):
        run(tmp_path, "--cache-out", str(out))
    assert sorted(shards.read_facts(out)) == ["o/d0", "o/d1", "o/d2"]


def test_cache_out_may_be_the_cache_dir_and_drops_removed(monkeypatch, tmp_path):
    setup(monkeypatch, 2)
    cache = write_cache(tmp_path, [0, 1, 5])  # o/d5 is no longer on the Hub
    assert run(tmp_path, "--cache", cache, "--cache-out", cache) == 0
    assert built == []
    from pathlib import Path

    got = shards.read_facts(Path(cache))
    assert sorted(got) == ["o/d0", "o/d1"]
    assert got["o/d0"]["reason"] == "cached"
    assert not list(Path(cache).glob("*.json"))  # the legacy shards were replaced


def test_line_shard_cache_is_read(monkeypatch, tmp_path, capsys):
    from dataclasses import asdict, replace

    setup(monkeypatch, 3)
    d = tmp_path / "c"
    shards.write_facts([asdict(replace(fact(f"o/d{i}"), revision=f"rev{i}")) for i in (0, 1)], d)
    assert run(tmp_path, "--cache", str(d)) == 0
    assert built == ["o/d2"]
    assert "progress fetched=1 reused=2 stale=0 deferred=0 total=3" in capsys.readouterr().out


def test_github_output_lines(monkeypatch, tmp_path):
    setup(monkeypatch, 4)
    gh = tmp_path / "gh_output"
    gh.write_text("earlier=1\n")
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh))
    ticks = iter([0, 0] + [10_000] * 50)
    assert run(tmp_path, "--deadline-minutes", "1", clock=lambda: next(ticks)) == 0
    assert gh.read_text() == "earlier=1\ndeferred=3\nfetched=1\n"
