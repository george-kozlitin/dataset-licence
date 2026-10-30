"""Hugging Face Hub client: paced, retrying, cached per run. No API key."""

from __future__ import annotations

import copy
import json
import re
import time
from collections.abc import Callable

import httpx

API = "https://huggingface.co/api"
SITE = "https://huggingface.co"
MISSING = (401, 403, 404)
MAX_RETRIES = 3
MAX_RETRY_AFTER = 60.0
MAX_WAIT = 330.0  # one 5-minute rate-limit window plus margin
_RL_R = re.compile(r"\br=(\d+)")
_RL_T = re.compile(r"\bt=(\d+)")


def bucket_of(url: str) -> str:
    """Hub rate-limit bucket for a URL: api, resolver or pages."""
    path = url.split("://", 1)[-1].split("/", 1)[-1]
    if path.startswith("api/"):
        return "api"
    if "/resolve/" in path:
        return "resolver"
    return "pages"


def parse_ratelimit(value: str | None) -> tuple[int | None, float | None]:
    """(remaining, seconds until reset) from a header like '"api";r=0;t=123'."""
    if not value:
        return None, None
    r = _RL_R.search(value)
    t = _RL_T.search(value)
    return (int(r.group(1)) if r else None, float(t.group(1)) if t else None)


class HubError(Exception):
    """Raised when the Hub keeps failing after the allowed retries."""


class Hub:
    def __init__(
        self,
        client: httpx.Client | None = None,
        rps: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._client = client or httpx.Client(timeout=30.0, follow_redirects=True)
        self._interval = 1.0 / rps
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None
        self._until: dict[str, float] = {}  # bucket -> clock time when it reopens
        self._cache: dict = {}

    # -- transport -------------------------------------------------------

    def _pace(self, bucket: str) -> None:
        reopen = self._until.get(bucket)
        if reopen is not None:
            wait = reopen - self._clock()
            if wait > 0:
                self._sleep(min(wait, MAX_WAIT))
            del self._until[bucket]
        if self._last is not None:
            wait = self._last + self._interval - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()

    def _get(
        self, url: str, params: dict | None = None, max_bytes: int | None = None
    ) -> tuple[httpx.Response, bytes] | None:
        """GET with pacing and retries. None for 401/403/404.

        Returns the response and its body; with max_bytes the body is streamed
        and reading stops once that many bytes arrived.
        """
        bucket = bucket_of(url)
        for attempt in range(MAX_RETRIES + 1):
            self._pace(bucket)
            delay = 2.0**attempt
            try:
                with self._client.stream("GET", url, params=params) as resp:
                    if resp.status_code < 400:
                        body = self._read(resp, max_bytes)
                    remaining, reset = parse_ratelimit(resp.headers.get("RateLimit"))
            except httpx.TransportError as exc:
                if attempt == MAX_RETRIES:
                    raise HubError(f"network error for {url}: {exc}") from exc
                self._sleep(delay)
                continue
            if 300 <= resp.status_code < 400:
                raise HubError(
                    f"unexpected redirect {resp.status_code} for {url}: the client must follow redirects"
                )
            if resp.status_code in MISSING:
                return None
            if resp.status_code < 300 and remaining == 0 and reset is not None:
                self._until[bucket] = self._clock() + min(reset, MAX_WAIT)
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == MAX_RETRIES:
                    raise HubError(f"{resp.status_code} for {url} after {MAX_RETRIES} retries")
                if reset is not None:
                    delay = reset + 1.0
                else:
                    try:
                        delay = max(0.0, float(resp.headers["Retry-After"]))
                    except (KeyError, ValueError):
                        pass
                    else:
                        delay = min(delay, MAX_RETRY_AFTER)
                self._sleep(min(delay, MAX_WAIT))
                continue
            if resp.status_code >= 400:
                raise HubError(f"{resp.status_code} for {url}")
            return resp, body
        raise HubError(f"unreachable: {url}")  # pragma: no cover

    @staticmethod
    def _read(resp: httpx.Response, max_bytes: int | None) -> bytes:
        if max_bytes is None:
            return resp.read()
        buf = bytearray()
        for chunk in resp.iter_bytes():
            buf += chunk
            if len(buf) >= max_bytes:
                break
        return bytes(buf[:max_bytes])

    def _cached(self, key, fetch):
        if key not in self._cache:
            self._cache[key] = fetch()
        return copy.deepcopy(self._cache[key])

    # -- API -------------------------------------------------------------

    def top_datasets(self, n: int, shape: Callable[[dict], dict] | None = None) -> list[dict]:
        """The n most downloaded datasets, or every dataset on the Hub when n <= 0.

        Pages are parsed one at a time and each entry goes through shape (when given) before the
        next page is read, so the full listing (megabytes of cardData per page) is never held at
        once. Not cached: a deep copy of hundreds of thousands of entries would double the memory.
        """
        out: list[dict] = []
        url: str | None = f"{API}/datasets"
        params: dict | None = {"sort": "downloads", "direction": "-1", "limit": "1000", "full": "true"}
        while url and (n <= 0 or len(out) < n):
            got = self._get(url, params)
            if got is None:
                raise HubError(f"listing unavailable: {url}")
            resp, body = got
            page = json.loads(body)
            del body
            if n > 0:
                page = page[: n - len(out)]
            out.extend(shape(e) if shape else e for e in page if isinstance(e, dict))
            del page
            url = resp.links.get("next", {}).get("url")
            params = None  # the next link already carries the query
        return out

    def dataset(self, id: str) -> dict | None:
        def fetch():
            got = self._get(f"{API}/datasets/{id}")
            return None if got is None else json.loads(got[1])

        return self._cached(("dataset", id), fetch)

    @staticmethod
    def files_from_entry(entry: dict) -> list[str]:
        """Top-level file names from a full=true list entry's siblings."""
        out = []
        for s in entry.get("siblings") or []:
            name = s.get("rfilename") if isinstance(s, dict) else None
            if isinstance(name, str) and "/" not in name:
                out.append(name)
        return out

    def files(self, id: str) -> list[str] | None:
        """Top-level file paths on main (directories are skipped)."""

        def fetch():
            got = self._get(f"{API}/datasets/{id}/tree/main")
            if got is None:
                return None
            return [e["path"] for e in json.loads(got[1]) if e.get("type") == "file"]

        return self._cached(("files", id), fetch)

    def raw(self, id: str, path: str, max_bytes: int | None = 2048) -> str | None:
        def fetch():
            got = self._get(f"{SITE}/datasets/{id}/resolve/main/{path}", max_bytes=max_bytes)
            if got is None:
                return None
            return got[1].decode("utf-8", errors="replace")

        return self._cached(("raw", id, path, max_bytes), fetch)

    def card(self, id: str) -> str | None:
        return self.raw(id, "README.md", max_bytes=None)
