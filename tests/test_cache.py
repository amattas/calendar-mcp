"""Tests for the cache facade and its memory/PostgreSQL backends.

The postgres parameter runs only when TEST_DATABASE_URL points at a scratch
database (see README.md, "Running the tests").
"""

import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import psycopg
import pytest

from src.services.cache import (
    Cache,
    CacheConfig,
    MemoryBackend,
    PostgresBackend,
    cache_aside,
    glob_to_like,
)

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class Harness:
    cache: Cache
    advance: Callable[[float], None]


@pytest.fixture(
    params=[
        "memory",
        pytest.param(
            "postgres",
            marks=pytest.mark.skipif(
                not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set"
            ),
        ),
    ]
)
def harness(request: pytest.FixtureRequest) -> Iterator[Harness]:
    if request.param == "memory":
        clock = FakeClock()
        yield Harness(Cache(MemoryBackend(clock=clock)), clock.advance)
        return
    assert TEST_DATABASE_URL
    cache = Cache(PostgresBackend(TEST_DATABASE_URL))
    assert cache.flush_all()
    yield Harness(cache, time.sleep)
    cache.flush_all()
    cache.close()


@pytest.mark.parametrize("value", ["text", "", {"a": [1, 2]}, [1, "x"], 0, False])
def test_round_trip(harness: Harness, value: Any) -> None:
    assert harness.cache.set("k", value, ttl=60)
    assert harness.cache.get("k", default="missing") == value
    assert harness.cache.get("absent", default="missing") == "missing"
    stats = harness.cache.get_stats()
    assert (stats.hits, stats.misses, stats.errors) == (1, 1, 0)


def test_ttl(harness: Harness) -> None:
    cache = harness.cache
    assert cache.set("short", "v", ttl=1)
    assert cache.set("forever", "v", ttl=None)
    assert cache.set("zero", "old", ttl=60)
    assert cache.set("zero", "new", ttl=0) is False
    assert cache.set("neg", "v", ttl=-5) is False
    assert cache.get("zero") is None
    assert cache.get("neg") is None
    assert cache.get("short") == "v"
    harness.advance(1.2)
    assert cache.get("short") is None
    assert cache.get("forever") == "v"


@pytest.mark.parametrize(
    ("pattern", "expected_left"),
    [
        ("ical:*", {"events:today", "a%c", "a_c", "50%_off_sale", "50xxoff"}),
        (
            "ical:events:*",
            {"ical:info", "events:today", "a%c", "a_c", "50%_off_sale", "50xxoff"},
        ),
        (
            "a?c",
            {
                "ical:info",
                "ical:events:today",
                "events:today",
                "50%_off_sale",
                "50xxoff",
            },
        ),
        (
            "50%_off*",
            {"ical:info", "ical:events:today", "events:today", "a%c", "a_c", "50xxoff"},
        ),
    ],
)
def test_delete_and_delete_pattern(
    harness: Harness, pattern: str, expected_left: set[str]
) -> None:
    keys = [
        "ical:info",
        "ical:events:today",
        "events:today",
        "a%c",
        "a_c",
        "50%_off_sale",
        "50xxoff",
    ]
    cache = harness.cache
    for key in keys:
        assert cache.set(key, key, ttl=60)
    deleted = cache.delete_pattern(pattern)
    left = {k for k in keys if cache.get(k) is not None}
    assert left == expected_left
    assert deleted == len(keys) - len(expected_left)
    survivor = min(left)
    assert cache.delete(survivor) is True
    assert cache.delete(survivor) is False


@pytest.mark.parametrize(
    ("glob", "like"),
    [
        ("ical:*", "ical:%"),
        ("a?c", "a_c"),
        ("50%_off*", "50\\%\\_off%"),
        ("back\\slash", "back\\\\slash"),
        ("[abc]", "[abc]"),
    ],
)
def test_glob_to_like(glob: str, like: str) -> None:
    assert glob_to_like(glob) == like


def test_upsert_overwrites_value_and_resets_ttl(harness: Harness) -> None:
    cache = harness.cache
    assert cache.set("k", "first", ttl=1)
    assert cache.set("k", "second", ttl=None)
    harness.advance(1.2)
    assert cache.get("k") == "second"
    assert cache.set("k", "third", ttl=1)
    harness.advance(1.2)
    assert cache.get("k") is None


class RaisingBackend:
    name = "stub"

    def is_connected(self) -> bool:
        return True

    def __getattr__(self, attr: str) -> Callable[..., Any]:
        def fail(*_: Any) -> Any:
            raise RuntimeError(f"{attr} exploded")

        return fail


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        (lambda c: c.get("k", default="d"), "d"),
        (lambda c: c.set("k", 1, ttl=5), False),
        (lambda c: c.delete("k"), False),
        (lambda c: c.delete_pattern("ical:*"), 0),
        (lambda c: c.flush_all(), False),
        (lambda c: c.info(), {"backend": "stub"}),
        (lambda c: c.close(), None),
    ],
)
def test_backend_failures_never_raise(
    call: Callable[[Cache], Any], expected: Any, caplog: pytest.LogCaptureFixture
) -> None:
    cache = Cache(RaisingBackend())  # type: ignore[arg-type]
    assert call(cache) == expected
    call(cache)
    assert cache.get_stats().errors == 2
    # Rate limited: two failures of the same method log once.
    assert len([r for r in caplog.records if r.levelname == "ERROR"]) == 1


class DownPool:
    def __init__(self) -> None:
        self.attempts = 0

    def open(self, wait: bool = False) -> None:
        pass

    def connection(self) -> Any:
        self.attempts += 1
        raise psycopg.OperationalError("connection refused")

    def close(self) -> None:
        pass


def test_postgres_backoff() -> None:
    clock = FakeClock()
    backend = PostgresBackend("postgresql://u@127.0.0.1:1/db", clock=clock)
    pool = DownPool()
    backend._pool = pool  # type: ignore[assignment]
    cache = Cache(backend)
    assert cache.is_connected()
    assert cache.get("k", default="d") == "d"
    assert cache.get_stats().errors == 1
    assert not cache.is_connected()
    # While backing off, ops short-circuit without touching the pool.
    assert cache.get("k") is None
    assert cache.set("k", "v") is False
    assert pool.attempts == 1
    clock.advance(30)
    assert cache.is_connected()
    assert cache.get("k") is None
    assert pool.attempts == 2


def test_flush_all_and_info(harness: Harness) -> None:
    cache = harness.cache
    for key in ("a", "b", "c"):
        assert cache.set(key, key, ttl=60)
    info = cache.info()
    assert info["backend"] in {"memory", "postgres"}
    assert set(info) >= {"server_version", "entries", "expired_pending", "table_bytes"}
    assert info["entries"] == 3
    assert cache.flush_all() is True
    assert cache.get("a") is None
    assert cache.info()["entries"] == 0


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        (
            {"CACHE_BACKEND": "memory", "DATABASE_URL": "postgresql://x/y"},
            MemoryBackend,
        ),
        ({"DATABASE_URL": "postgresql://u:pw@127.0.0.1:1/db"}, PostgresBackend),
        ({}, None),
    ],
)
def test_from_env(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: type | None
) -> None:
    monkeypatch.delenv("CACHE_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    cache = Cache.from_env()  # must not attempt a connection
    if expected is None:
        assert cache is None
    else:
        assert cache is not None
        assert isinstance(cache._backend, expected)
        cache.close()


def test_cache_aside_second_call_hits() -> None:
    calls: list[int] = []

    class Client:
        cache = Cache(MemoryBackend())

        @cache_aside(CacheConfig(ttl=60, key_prefix="ical:info"))
        def get_calendar_info(self) -> list[str]:
            calls.append(1)
            return ["work"]

    client = Client()
    assert client.get_calendar_info() == ["work"]
    assert client.get_calendar_info() == ["work"]
    assert len(calls) == 1
    assert Client.cache.get_stats().hits == 1


@pytest.fixture
def server_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from src import server

    monkeypatch.setattr(server, "_cache_service", Cache(MemoryBackend()))
    yield server


def test_clear_cache_tool_by_pattern(server_cache: Any) -> None:
    cache = server_cache._cache_service
    for key in ("ical:events:v1:today", "ical:info:v1", "other"):
        cache.set(key, 1, ttl=60)
    result = server_cache.clear_cache("ical:*")
    assert result == {"status": "success", "pattern": "ical:*", "keys_deleted": 2}
    assert cache.get("other") == 1
    assert server_cache.clear_cache() == {
        "status": "success",
        "message": "All cache cleared",
    }
    assert cache.get("other") is None


@pytest.mark.parametrize(
    ("url", "database"),
    [
        (
            "postgresql://svc:s3cret@10.236.0.3:5432/calendar_mcp?sslmode=require",
            {"host": "10.236.0.3", "dbname": "calendar_mcp"},
        ),
        ("host=10.236.0.3 password=s3cret dbname", {"host": "", "dbname": ""}),
    ],
)
def test_get_cache_info_tool_hides_credentials(
    server_cache: Any, monkeypatch: pytest.MonkeyPatch, url: str, database: Any
) -> None:
    monkeypatch.setenv("DATABASE_URL", url)
    backend = PostgresBackend(url)
    backend._down_until = float("inf")
    monkeypatch.setattr(server_cache, "_cache_service", Cache(backend))
    info = server_cache.get_cache_info()
    assert info["connected"] is False
    assert info["backend"] == "postgres"
    assert info["database"] == database
    assert "s3cret" not in repr(info)
    assert set(info) == {
        "connected",
        "backend",
        "database",
        "server_version",
        "entries",
        "table_bytes",
    }
