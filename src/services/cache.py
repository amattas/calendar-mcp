"""Cache layer for Calendar MCP services.

`Cache` is the facade the app uses. It serializes values as JSON, keeps
hit/miss statistics, and never raises into the request path: backend errors are
logged (rate limited), counted in `CacheStats.errors`, and turned into misses.

Backends:
- `PostgresBackend`: a table in the service's own PostgreSQL database
  (`DATABASE_URL`). Connects lazily, backs off for 30 s after any database
  error, and creates its table on first use.
- `MemoryBackend`: a process-local dict for local development and tests
  (`CACHE_BACKEND=memory`).
"""

import hashlib
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Protocol, cast

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg_pool import ConnectionPool, PoolTimeout

logger = logging.getLogger(__name__)

APPLICATION_NAME = "calendar-mcp"


@dataclass
class CacheStats:
    """Cache performance metrics"""

    hits: int = 0
    misses: int = 0
    errors: int = 0
    total_requests: int = 0
    hit_time_sum: float = 0.0
    miss_time_sum: float = 0.0
    last_reset: float = field(default_factory=time.time)

    @property
    def hit_rate(self) -> float:
        """Calculate cache hit rate"""
        if self.total_requests == 0:
            return 0.0
        return self.hits / self.total_requests

    @property
    def miss_rate(self) -> float:
        """Calculate cache miss rate"""
        if self.total_requests == 0:
            return 0.0
        return self.misses / self.total_requests

    @property
    def avg_hit_time(self) -> float:
        """Average time for cache hits in milliseconds"""
        if self.hits == 0:
            return 0.0
        return (self.hit_time_sum / self.hits) * 1000

    @property
    def avg_miss_time(self) -> float:
        """Average time for cache misses in milliseconds"""
        if self.misses == 0:
            return 0.0
        return (self.miss_time_sum / self.misses) * 1000

    def to_dict(self) -> dict[str, Any]:
        """Convert stats to dictionary"""
        return {
            "hits": self.hits,
            "misses": self.misses,
            "errors": self.errors,
            "total_requests": self.total_requests,
            "hit_rate": self.hit_rate,
            "miss_rate": self.miss_rate,
            "avg_hit_time_ms": self.avg_hit_time,
            "avg_miss_time_ms": self.avg_miss_time,
            "uptime_seconds": time.time() - self.last_reset,
        }

    def reset(self) -> None:
        """Reset all statistics"""
        self.hits = 0
        self.misses = 0
        self.errors = 0
        self.total_requests = 0
        self.hit_time_sum = 0.0
        self.miss_time_sum = 0.0
        self.last_reset = time.time()


@dataclass
class CacheConfig:
    """Configuration for cache behavior"""

    ttl: int = 300  # Default 5 minutes
    key_prefix: str = ""
    serialize_json: bool = True  # Use JSON serialization
    compress: bool = False  # Future: add compression support
    version: str = "v1"  # Cache version for key generation

    def get_ttl_seconds(self) -> int:
        """Get TTL in seconds"""
        return self.ttl


def glob_to_like(glob: str) -> str:
    """Translate a cache-key glob into a SQL LIKE pattern (used with ESCAPE '\\').

    `*` matches any run of characters and `?` any single character; every other
    character is literal, so `%`, `_` and `\\` in the glob are escaped.
    Character classes (`[abc]`) are not supported and match literally.
    """
    out: list[str] = []
    for ch in glob:
        if ch == "*":
            out.append("%")
        elif ch == "?":
            out.append("_")
        elif ch in ("\\", "%", "_"):
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def _glob_to_regex(glob: str) -> re.Pattern[str]:
    """Same matching rules as `glob_to_like`, for the memory backend."""
    parts = [".*" if ch == "*" else "." if ch == "?" else re.escape(ch) for ch in glob]
    return re.compile("".join(parts), re.DOTALL)


def describe_database_url(url: str) -> dict[str, str]:
    """Return only the host and database name of a libpq URL (never credentials)."""
    try:
        params = conninfo_to_dict(url)
    except psycopg.Error:
        # The parser's message can echo the URL, password included: drop it.
        return {"host": "", "dbname": ""}
    return {
        "host": str(params.get("host", "")),
        "dbname": str(params.get("dbname", "")),
    }


class CacheBackend(Protocol):
    """Storage behind `Cache`. Values are already-serialized JSON text."""

    name: str

    def is_connected(self) -> bool: ...
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str, ttl: int | None) -> None: ...
    def delete(self, key: str) -> bool: ...
    def delete_pattern(self, glob: str) -> int: ...
    def flush(self) -> int: ...
    def info(self) -> dict[str, Any]: ...
    def close(self) -> None: ...


class MemoryBackend:
    """Process-local backend for local development and tests."""

    name = "memory"

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._data: dict[str, tuple[str, float | None]] = {}
        self._lock = threading.Lock()

    def _live(self, entry: tuple[str, float | None], now: float) -> bool:
        return entry[1] is None or entry[1] > now

    def is_connected(self) -> bool:
        return True

    def get(self, key: str) -> str | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if not self._live(entry, self._clock()):
                del self._data[key]
                return None
            return entry[0]

    def set(self, key: str, value: str, ttl: int | None) -> None:
        expires = None if ttl is None else self._clock() + ttl
        with self._lock:
            self._data[key] = (value, expires)

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._data.pop(key, None) is not None

    def delete_pattern(self, glob: str) -> int:
        regex = _glob_to_regex(glob)
        with self._lock:
            doomed = [k for k in self._data if regex.fullmatch(k)]
            for key in doomed:
                del self._data[key]
            return len(doomed)

    def flush(self) -> int:
        with self._lock:
            count = len(self._data)
            self._data.clear()
            return count

    def info(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            expired = sum(1 for e in self._data.values() if not self._live(e, now))
            return {
                "server_version": None,
                "entries": len(self._data),
                "expired_pending": expired,
                "table_bytes": None,
            }

    def close(self) -> None:
        pass


# The ics-combiner copy of this module uses a logged table ("TABLE"); this
# cache only holds data the calendar feeds can serve again, so a crash may lose it.
_TABLE_KIND = "UNLOGGED TABLE"
_SCHEMA_LOCK_ID = 727001
_BACKOFF_SECONDS = 30.0
_SWEEP_EVERY = 50

_SQL_LOCK = "SELECT pg_advisory_xact_lock(%s)"
_SQL_CREATE_TABLE = (
    f"CREATE {_TABLE_KIND} IF NOT EXISTS cache_entries "
    "(key text PRIMARY KEY, value text NOT NULL, expires_at timestamptz)"
)
_SQL_CREATE_INDEX = (
    "CREATE INDEX IF NOT EXISTS cache_entries_expires_idx "
    "ON cache_entries (expires_at) WHERE expires_at IS NOT NULL"
)
_SQL_GET = (
    "SELECT value FROM cache_entries "
    "WHERE key = %s AND (expires_at IS NULL OR expires_at > now())"
)
_SQL_SET = (
    "INSERT INTO cache_entries (key, value, expires_at) "
    "VALUES (%s, %s, now() + %s::double precision * interval '1 second') "
    "ON CONFLICT (key) DO UPDATE "
    "SET value = EXCLUDED.value, expires_at = EXCLUDED.expires_at"
)
_SQL_DELETE = "DELETE FROM cache_entries WHERE key = %s"
_SQL_DELETE_LIKE = "DELETE FROM cache_entries WHERE key LIKE %s ESCAPE '\\'"
_SQL_FLUSH = "DELETE FROM cache_entries"
_SQL_SWEEP = (
    "DELETE FROM cache_entries WHERE key IN (SELECT key FROM cache_entries "
    "WHERE expires_at IS NOT NULL AND expires_at <= now() LIMIT 500)"
)
_SQL_INFO = (
    "SELECT version(), (SELECT count(*) FROM cache_entries), "
    "(SELECT count(*) FROM cache_entries "
    "WHERE expires_at IS NOT NULL AND expires_at <= now()), "
    "pg_total_relation_size('cache_entries')"
)


class _DropPoolConnectErrors(logging.Filter):
    """Drop psycopg_pool's per-attempt "error connecting" warnings.

    The pool retries in the background about once a second during an outage,
    and libpq's message can echo parts of the URL. `Cache` already logs the
    outage, rate limited, without the URL.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.getMessage().startswith("error connecting")


logging.getLogger("psycopg.pool").addFilter(_DropPoolConnectErrors())


class PostgresBackend:
    """Cache table in PostgreSQL via a small sync connection pool.

    Constructing it does no I/O. The pool opens on the first operation, the
    table is created once per process after the first successful connect, and
    any database error starts a 30 s backoff during which every operation is a
    miss and `is_connected()` is False (it never does I/O itself).
    """

    name = "postgres"

    def __init__(
        self, conninfo: str, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._opened = False
        self._schema_ready = False
        self._down_until = 0.0
        self._sets = 0
        self._pool: ConnectionPool | None = None
        try:
            conninfo_to_dict(conninfo)
        except psycopg.Error as e:
            # Never log str(e): libpq echoes the URL, password included.
            logger.error(
                "DATABASE_URL is not a valid libpq connection string (%s); "
                "cache disabled",
                type(e).__name__,
            )
            return
        self._pool = ConnectionPool(
            conninfo,
            min_size=1,
            max_size=3,
            timeout=2,
            open=False,
            kwargs={
                "connect_timeout": 3,
                "options": "-c statement_timeout=3000",
                "autocommit": True,
                "application_name": APPLICATION_NAME,
            },
            check=ConnectionPool.check_connection,
        )

    def is_connected(self) -> bool:
        """False when unconfigured (invalid URL) or backing off; no I/O."""
        return self._pool is not None and self._clock() >= self._down_until

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection[Any]]:
        pool = self._pool
        if pool is None:
            raise RuntimeError("cache database is not configured")
        try:
            with self._lock:
                if not self._opened:
                    pool.open(wait=False)
                    self._opened = True
            with pool.connection() as conn:
                if not self._schema_ready:
                    self._ensure_schema(conn)
                yield conn
        except psycopg.Error, PoolTimeout:
            self._down_until = self._clock() + _BACKOFF_SECONDS
            raise

    def _ensure_schema(self, conn: psycopg.Connection[Any]) -> None:
        with self._lock:
            if self._schema_ready:
                return
            try:
                with conn.transaction():
                    conn.execute(_SQL_LOCK, (_SCHEMA_LOCK_ID,))
                    conn.execute(_SQL_CREATE_TABLE)
                    conn.execute(_SQL_CREATE_INDEX)
            except psycopg.errors.InsufficientPrivilege:
                logger.warning(
                    "Cache table cannot be created: role %r lacks CREATE on schema "
                    "public. Fix as postgres: GRANT ALL ON SCHEMA public TO %s",
                    conn.info.user,
                    conn.info.user,
                )
                raise
            self._schema_ready = True

    def get(self, key: str) -> str | None:
        if not self.is_connected():
            return None
        with self._connection() as conn:
            row = conn.execute(_SQL_GET, (key,)).fetchone()
        return None if row is None else str(row[0])

    def set(self, key: str, value: str, ttl: int | None) -> None:
        if not self.is_connected():
            return
        with self._connection() as conn:
            conn.execute(_SQL_SET, (key, value, ttl))
            self._sets += 1
            if self._sets % _SWEEP_EVERY == 0:
                conn.execute(_SQL_SWEEP)

    def delete(self, key: str) -> bool:
        if not self.is_connected():
            return False
        with self._connection() as conn:
            return conn.execute(_SQL_DELETE, (key,)).rowcount > 0

    def delete_pattern(self, glob: str) -> int:
        if not self.is_connected():
            return 0
        with self._connection() as conn:
            return conn.execute(_SQL_DELETE_LIKE, (glob_to_like(glob),)).rowcount

    def flush(self) -> int:
        if not self.is_connected():
            return 0
        with self._connection() as conn:
            return conn.execute(_SQL_FLUSH).rowcount

    def info(self) -> dict[str, Any]:
        if not self.is_connected():
            return {}
        with self._connection() as conn:
            row = conn.execute(_SQL_INFO).fetchone()
        if row is None:
            return {}
        version, entries, expired, size = row
        return {
            "server_version": version,
            "entries": entries,
            "expired_pending": expired,
            "table_bytes": size,
        }

    def close(self) -> None:
        with self._lock:
            if self._opened and self._pool is not None:
                self._pool.close()
                self._opened = False


_ERROR_LOG_INTERVAL = 60.0


class Cache:
    """Cache facade: JSON values, statistics, and a never-raise error contract."""

    def __init__(self, backend: CacheBackend) -> None:
        self._backend = backend
        self.stats = CacheStats()
        self._last_error_log: dict[str, float] = {}

    @classmethod
    def from_env(cls) -> Cache | None:
        """`CACHE_BACKEND=memory`, else `DATABASE_URL`, else no cache.

        Does not connect: the PostgreSQL pool opens on first use.
        """
        if os.getenv("CACHE_BACKEND", "").strip().lower() == "memory":
            return cls(MemoryBackend())
        url = os.getenv("DATABASE_URL")
        if url:
            return cls(PostgresBackend(url))
        return None

    def _failed(self, method: str, exc: Exception) -> None:
        self.stats.errors += 1
        now = time.monotonic()
        last = self._last_error_log.get(method)
        if last is None or now - last >= _ERROR_LOG_INTERVAL:
            self._last_error_log[method] = now
            logger.error(
                "Cache %s failed (%s backend): %s: %s",
                method,
                self._backend.name,
                type(exc).__name__,
                exc,
            )

    def is_connected(self) -> bool:
        """True unless the backend is backing off after an error (no I/O)."""
        try:
            return self._backend.is_connected()
        except Exception as e:
            self._failed("is_connected", e)
            return False

    def get(self, key: str, default: Any = None) -> Any:
        start = time.perf_counter()
        try:
            data = self._backend.get(key)
            value = default if data is None else json.loads(data)
        except Exception as e:
            self._failed("get", e)
            return default
        elapsed = time.perf_counter() - start
        self.stats.total_requests += 1
        if data is None:
            self.stats.misses += 1
            self.stats.miss_time_sum += elapsed
        else:
            self.stats.hits += 1
            self.stats.hit_time_sum += elapsed
        return value

    def set(self, key: str, value: Any, ttl: int | None = None) -> bool:
        """Store `value` for `ttl` seconds (None: no expiry; <= 0: delete, False)."""
        try:
            if ttl is not None and ttl <= 0:
                self._backend.delete(key)
                return False
            if not self._backend.is_connected():
                return False
            self._backend.set(key, json.dumps(value), ttl)
            return True
        except Exception as e:
            self._failed("set", e)
            return False

    def delete(self, key: str) -> bool:
        try:
            return self._backend.delete(key)
        except Exception as e:
            self._failed("delete", e)
            return False

    def delete_pattern(self, pattern: str) -> int:
        """Delete keys matching a glob (`*`, `?`; no character classes)."""
        try:
            if not self._backend.is_connected():
                return 0
            return self._backend.delete_pattern(pattern)
        except Exception as e:
            self._failed("delete_pattern", e)
            return 0

    def flush_all(self) -> bool:
        """Delete every cache entry."""
        try:
            if not self._backend.is_connected():
                return False
            self._backend.flush()
            return True
        except Exception as e:
            self._failed("flush_all", e)
            return False

    def info(self) -> dict[str, Any]:
        """Backend name plus server_version, entries, expired_pending, table_bytes."""
        try:
            return {"backend": self._backend.name, **self._backend.info()}
        except Exception as e:
            self._failed("info", e)
            return {"backend": self._backend.name}

    def get_stats(self) -> CacheStats:
        return self.stats

    def reset_stats(self) -> None:
        self.stats.reset()

    def close(self) -> None:
        try:
            self._backend.close()
        except Exception as e:
            self._failed("close", e)


def cache_key_generator(
    prefix: str, version: str = "v1", *args: Any, **kwargs: Any
) -> str:
    """
    Generate cache key from function arguments

    Args:
        prefix: Key prefix (e.g., "ical:events")
        version: Cache version
        *args: Positional arguments
        **kwargs: Keyword arguments

    Returns:
        Cache key string
    """
    # Build key components
    parts = [prefix, version]

    # Add positional arguments
    for arg in args:
        if arg is not None:
            parts.append(str(arg))

    # Add keyword arguments (sorted for consistency)
    if kwargs:
        # Filter out None values and create sorted param string
        params = {k: v for k, v in kwargs.items() if v is not None}
        if params:
            param_str = json.dumps(params, sort_keys=True, default=str)
            # MD5 used only to shorten cache key component (not for security)
            param_hash = hashlib.md5(
                param_str.encode(), usedforsecurity=False
            ).hexdigest()[:8]
            parts.append(param_hash)

    return ":".join(parts)


def cache_aside[**P, T](
    config: CacheConfig | None = None,
    cache_instance: Cache | None = None,
    key_func: Callable[..., str] | None = None,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """
    Decorator for cache-aside pattern

    Args:
        config: Cache configuration
        cache_instance: Cache instance to use
        key_func: Custom key generation function

    Example:
        @cache_aside(CacheConfig(ttl=60, key_prefix="ical:events"))
        def get_events(calendar_id: str):
            # Expensive API call
            return api.get_events(calendar_id)
    """
    cfg = config if config is not None else CacheConfig()

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            # Get cache instance
            cache = cache_instance
            # Try to get from first argument if it has a cache attribute
            if cache is None and args and hasattr(args[0], "cache"):
                cache = args[0].cache

            # If no cache available, just call function
            if cache is None or not cache.is_connected():
                return func(*args, **kwargs)

            # Generate cache key
            if key_func:
                cache_key = key_func(*args, **kwargs)
            else:
                # Skip 'self' argument for instance methods
                key_args = args[1:] if args and hasattr(args[0], "__class__") else args
                cache_key = cache_key_generator(
                    cfg.key_prefix or func.__name__,
                    cfg.version,
                    *key_args,
                    **kwargs,
                )

            # Try to get from cache
            start_time = time.time()
            cached_value = cache.get(cache_key)

            if cached_value is not None:
                logger.debug(
                    f"Cache hit for {cache_key} ({time.time() - start_time:.3f}s)"
                )
                return cast(T, cached_value)

            # Cache miss - call function
            logger.debug(f"Cache miss for {cache_key}")
            result = func(*args, **kwargs)

            # Store in cache
            if result is not None:
                cache.set(cache_key, result, ttl=cfg.get_ttl_seconds())

            return result

        # Add cache management methods to wrapper
        def invalidate(*args: Any, **kwargs: Any) -> bool:
            return _invalidate_cache(
                cache_instance, cfg, key_func, func, *args, **kwargs
            )

        setattr(wrapper, "invalidate", invalidate)  # noqa: B010 - mypy rejects new attributes on a function

        return wrapper

    return decorator


def _invalidate_cache(
    cache_instance: Cache | None,
    config: CacheConfig,
    key_func: Callable[..., str] | None,
    func: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> bool:
    """Invalidate cache for specific function call"""
    cache = cache_instance
    if cache is None and args and hasattr(args[0], "cache"):
        cache = args[0].cache

    if cache is None:
        return False

    if key_func:
        cache_key = key_func(*args, **kwargs)
    else:
        key_args = args[1:] if args and hasattr(args[0], "__class__") else args
        cache_key = cache_key_generator(
            config.key_prefix or func.__name__, config.version, *key_args, **kwargs
        )

    return cache.delete(cache_key)


def _get_cache_ttl(env_var: str, default: int) -> int:
    """Get TTL value from environment or use default"""
    env_value = os.getenv(f"CACHE_TTL_{env_var}")
    if env_value:
        try:
            value = int(env_value)
            if value < 0:
                logger.warning(
                    f"Invalid negative TTL for CACHE_TTL_{env_var}: {value}, using default: {default}"
                )
                return default
            logger.debug(
                f"Using custom TTL for {env_var}: {value} seconds (default was {default})"
            )
            return value
        except ValueError:
            logger.warning(
                f"Invalid TTL value for CACHE_TTL_{env_var}: {env_value}, using default: {default}"
            )
    return default


# TTL constants for different data types (in seconds)
class CacheTTL:
    """
    Standard TTL values for different data types.

    All values can be overridden via environment variables by prefixing with CACHE_TTL_
    For example: CACHE_TTL_CALENDAR_EVENTS=300 will set CALENDAR_EVENTS to 300 seconds

    Environment Variables:
        CACHE_TTL_CALENDAR_EVENTS: Calendar events cache (default: 900 seconds)
        CACHE_TTL_CALENDAR_INFO: Calendar info cache (default: 1800 seconds)
    """

    # Calendar
    CALENDAR_EVENTS = _get_cache_ttl("CALENDAR_EVENTS", 900)  # 15 minutes default
    CALENDAR_INFO = _get_cache_ttl("CALENDAR_INFO", 1800)  # 30 minutes default
