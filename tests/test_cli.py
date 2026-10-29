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


def setup(monkeypatch, n, failing=(), signals=None, list_error=False, unexpected=()):
    FakeHub.listing = [{"id": f"o/d{i}"} for i in range(n)]
    FakeHub.list_error = list_error
    bad = {f"o/d{i}" for i in failing}
    odd = {f"o/d{i}" for i in unexpected}

    def fake_build(entry, hub):
        if entry["id"] in bad:
            raise HubError("boom " + entry["id"])
        if entry["id"] in odd:
            raise KeyError("weird")
        sig = (signals or {}).get(entry["id"], "permissive")
        return fact(entry["id"], sig)

    monkeypatch.setattr(cli, "build_facts", fake_build)


def run(tmp_path, *extra):
    args = ["build", "--out", str(tmp_path / "site"), "--base-url", "https://x.test", *extra]
    return cli.main(args, hub_factory=FakeHub)


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
