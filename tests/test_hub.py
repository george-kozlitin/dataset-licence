import json
from pathlib import Path

import httpx
import pytest

from dlf.hub import Hub, HubError

FIX = Path(__file__).parent / "fixtures" / "hub"


def load(name):
    return json.loads((FIX / name).read_text())


class Env:
    """Fake clock plus sleep that advances it, and a request log."""

    def __init__(self, handler, rps=5.0):
        self.now = 1000.0
        self.sleeps = []
        self.requests = []

        def h(request):
            self.requests.append(request)
            return handler(request)

        self.hub = Hub(
            client=httpx.Client(transport=httpx.MockTransport(h)),
            rps=rps,
            sleep=self.sleep,
            clock=lambda: self.now,
        )

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s


NEXT = "https://huggingface.co/api/datasets?sort=downloads&direction=-1&limit=1000&full=true&cursor=abc"


def test_top_datasets_paginates_and_cuts_mid_page():
    def handler(req):
        if "cursor" not in req.url.params:
            assert req.url.params["sort"] == "downloads"
            assert req.url.params["direction"] == "-1"
            assert req.url.params["limit"] == "1000"
            assert req.url.params["full"] == "true"
            return httpx.Response(
                200,
                json=load("page1.json"),
                headers={"Link": f'<{NEXT}>; rel="next"'},
            )
        assert str(req.url) == NEXT
        return httpx.Response(200, json=load("page2.json"))

    env = Env(handler)
    out = env.hub.top_datasets(3)
    assert [d["id"] for d in out] == ["a/one", "two", "b/three"]
    assert out[0]["cardData"] == {"license": "mit"}
    assert len(env.requests) == 2


def test_top_datasets_stops_when_no_next_link():
    env = Env(lambda r: httpx.Response(200, json=load("page2.json")))
    assert len(env.hub.top_datasets(10)) == 2
    assert len(env.requests) == 1


def test_top_datasets_does_not_fetch_second_page_when_first_suffices():
    env = Env(
        lambda r: httpx.Response(
            200, json=load("page1.json"), headers={"Link": '<https://huggingface.co/api/datasets?page=2>; rel="next"'}
        )
    )
    assert len(env.hub.top_datasets(2)) == 2
    assert len(env.requests) == 1


def test_top_datasets_zero_lists_every_page_and_shapes_entries():
    def handler(req):
        if "cursor" not in req.url.params:
            return httpx.Response(200, json=load("page1.json"), headers={"Link": f'<{NEXT}>; rel="next"'})
        return httpx.Response(200, json=load("page2.json"))

    env = Env(handler)
    out = env.hub.top_datasets(0, shape=lambda e: {"id": e["id"]})
    assert out == [{"id": "a/one"}, {"id": "two"}, {"id": "b/three"}, {"id": "c/four"}]
    assert len(env.requests) == 2


def test_top_datasets_is_not_cached():
    env = Env(lambda r: httpx.Response(200, json=load("page2.json")))
    env.hub.top_datasets(0)
    env.hub.top_datasets(0)
    assert len(env.requests) == 2
    assert env.hub._cache == {}


@pytest.mark.parametrize("status", [401, 403, 404])
def test_dataset_missing_maps_to_none(status):
    env = Env(lambda r: httpx.Response(status))
    assert env.hub.dataset("x/y") is None
    assert env.requests[0].url.path == "/api/datasets/x/y"


def test_dataset_ok():
    env = Env(lambda r: httpx.Response(200, json={"id": "x/y"}))
    assert env.hub.dataset("x/y") == {"id": "x/y"}


def test_files_top_level_paths():
    def handler(req):
        assert req.url.path == "/api/datasets/x/y/tree/main"
        return httpx.Response(200, json=load("tree.json"))

    env = Env(handler)
    assert env.hub.files("x/y") == ["README.md", "LICENSE"]


def test_files_missing_is_none():
    assert Env(lambda r: httpx.Response(404)).hub.files("x/y") is None


def test_card_and_raw():
    def handler(req):
        assert req.url.host == "huggingface.co"
        assert req.url.path.startswith("/datasets/x/y/resolve/main/")
        return httpx.Response(200, content="héllo wörld".encode())

    env = Env(handler)
    assert env.hub.card("x/y") == "héllo wörld"
    assert env.requests[0].url.path == "/datasets/x/y/resolve/main/README.md"


def test_raw_truncates_and_replaces_bad_bytes():
    env = Env(lambda r: httpx.Response(200, content=b"abcdef\xff\xfe"))
    assert env.hub.raw("x/y", "LICENSE", max_bytes=4) == "abcd"
    env2 = Env(lambda r: httpx.Response(200, content=b"ab\xffcd"))
    assert env2.hub.raw("x/y", "LICENSE") == "ab�cd"


@pytest.mark.parametrize("status", [401, 403, 404])
def test_raw_missing_is_none(status):
    env = Env(lambda r: httpx.Response(status))
    assert env.hub.raw("x/y", "LICENSE") is None
    assert env.hub.card("x/y") is None


def test_429_then_200_uses_retry_after():
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"id": "x/y"})

    env = Env(handler)
    assert env.hub.dataset("x/y") == {"id": "x/y"}
    assert len(calls) == 2
    assert 7 in env.sleeps


def test_backoff_without_retry_after_is_1_2_4():
    codes = [503, 503, 503, 200]

    def handler(req):
        c = codes.pop(0)
        return httpx.Response(c, json={"id": "x/y"})

    env = Env(handler, rps=1000.0)
    assert env.hub.dataset("x/y") == {"id": "x/y"}
    assert [s for s in env.sleeps if s >= 1] == [1, 2, 4]


def test_persistent_503_raises_after_three_retries():
    env = Env(lambda r: httpx.Response(503))
    with pytest.raises(HubError):
        env.hub.dataset("x/y")
    assert len(env.requests) == 4  # first try plus 3 retries


def test_transport_error_is_retried():
    n = []

    def handler(req):
        n.append(1)
        if len(n) < 3:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json={"id": "x/y"})

    env = Env(handler, rps=1000.0)
    assert env.hub.dataset("x/y") == {"id": "x/y"}
    assert len(n) == 3


def test_transport_error_persistent_raises_hub_error():
    def handler(req):
        raise httpx.ConnectError("boom")

    with pytest.raises(HubError):
        Env(handler, rps=1000.0).hub.dataset("x/y")


def test_other_client_error_raises():
    with pytest.raises(HubError):
        Env(lambda r: httpx.Response(400)).hub.dataset("x/y")


def test_pacing_sleeps_at_least_one_over_rps():
    env = Env(lambda r: httpx.Response(200, json={}), rps=5.0)
    env.hub.dataset("a/1")
    assert env.sleeps == []  # first call is not delayed
    env.hub.dataset("a/2")
    assert env.sleeps and sum(env.sleeps) >= 0.2 - 1e-9
    n = len(env.sleeps)
    env.now += 10  # long idle: no extra sleep needed
    env.hub.dataset("a/3")
    assert len(env.sleeps) == n


def test_cache_hit_makes_no_second_request():
    env = Env(lambda r: httpx.Response(200, json={"id": "x/y"}))
    env.hub.dataset("x/y")
    env.hub.dataset("x/y")
    assert len(env.requests) == 1


def test_cache_remembers_missing():
    env = Env(lambda r: httpx.Response(404))
    assert env.hub.dataset("x/y") is None
    assert env.hub.dataset("x/y") is None
    assert len(env.requests) == 1


def test_cache_returns_independent_copies():
    env = Env(lambda r: httpx.Response(200, json={"id": "x/y"}))
    a = env.hub.dataset("x/y")
    a["id"] = "mutated"
    assert env.hub.dataset("x/y") == {"id": "x/y"}


def test_retry_after_is_clamped():
    for header, expected in (("100000", 60), ("-5", 0)):
        codes = [429, 200]

        def handler(req, codes=codes, header=header):
            return httpx.Response(codes.pop(0), json={}, headers={"Retry-After": header})

        env = Env(handler, rps=1000.0)
        env.hub.dataset("x/y")
        assert expected in env.sleeps
        assert max(env.sleeps) <= 60 and min(env.sleeps) >= 0


def test_raw_stops_reading_after_max_bytes():
    read = []

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(1024):
                read.append(1)
                yield b"x" * 1024

    env = Env(lambda r: httpx.Response(200, stream=Body()))
    out = env.hub.raw("x/y", "LICENSE", max_bytes=2048)
    assert len(out) <= 2048
    assert len(read) < 10  # nowhere near the full 1 MB


def test_redirect_reaching_code_is_hub_error():
    env = Env(lambda r: httpx.Response(302, headers={"Location": "https://example.com"}))
    with pytest.raises(HubError, match="redirect"):
        env.hub.dataset("x/y")


def test_files_from_entry_top_level_strings_only():
    e = {"siblings": [{"rfilename": "README.md"}, {"rfilename": "data/x.csv"}, {"rfilename": 5}, "bad", {}]}
    assert Hub.files_from_entry(e) == ["README.md"]
    assert Hub.files_from_entry({}) == []


def test_resolver_url_used_for_license_file():
    env = Env(lambda r: httpx.Response(200, content=b"x"))
    env.hub.raw("x/y", "LICENSE")
    assert env.requests[0].url.path == "/datasets/x/y/resolve/main/LICENSE"


def test_429_waits_ratelimit_t_plus_one():
    codes = [429, 200]

    def handler(req):
        c = codes.pop(0)
        return httpx.Response(c, json={}, headers={"RateLimit": '"api";r=0;t=123', "Retry-After": "5"})

    env = Env(handler)
    env.hub.dataset("x/y")
    assert 124.0 in env.sleeps


def test_ratelimit_wait_is_capped():
    codes = [429, 200]
    env = Env(lambda r: httpx.Response(codes.pop(0), json={}, headers={"RateLimit": '"api";r=0;t=9999'}))
    env.hub.dataset("x/y")
    assert max(env.sleeps) == 330.0


def test_429_without_ratelimit_falls_back_to_retry_after():
    codes = [429, 200]
    env = Env(lambda r: httpx.Response(codes.pop(0), json={}, headers={"Retry-After": "7"}))
    env.hub.dataset("x/y")
    assert 7.0 in env.sleeps


def test_success_with_r0_sleeps_before_next_request_same_bucket_only():
    def handler(req):
        if req.url.path == "/api/datasets/a":
            return httpx.Response(200, json={}, headers={"RateLimit": '"api";r=0;t=100'})
        return httpx.Response(200, json={})

    env = Env(handler)
    env.hub.dataset("a")
    env.hub.raw("x/y", "LICENSE")  # resolver bucket: no wait
    assert max(env.sleeps, default=0) < 100
    env.hub.dataset("b")  # api bucket: waits out the window
    assert any(99 <= s <= 100 for s in env.sleeps)
