# Calendar MCP Server - Agent Development Guide

This document provides technical details for AI agents and assistants working with the Calendar MCP Server codebase.

## Project Overview

This is a **Model Context Protocol (MCP) server** for calendar management. The server integrates iCalendar feed management with optional PostgreSQL-backed caching.

### Core Services

- **iCalendar** (`services/ical.py`): Multi-calendar feed management with recurring event support
- **Cache** (`services/cache.py`): Cache facade over a PostgreSQL or in-memory backend, with cache-aside pattern

### Deployment

**HTTP mode** (`server_remote.py`): HTTP API with dual-factor authentication

## Architecture

### Entry Point

**HTTP Mode:**
- File: `server_remote.py`
- Transport: HTTP
- Use: Remote access, production deployment
- Port: 8080 (default)
- Authentication: Dual-factor path (API key + MD5 hash)

### Service Layer (`services/`)

**Calendar Service** (`services/ical.py`):
```python
class MultiCalendarService:
    - Manages multiple named calendar feeds
    - Uses recurring-ical-events for RRULE expansion
    - Automatic refresh timer (configurable interval)
    - Thread-safe with threading.Lock
    - Each feed has unique ID based on URL hash
```

**Cache Service** (`services/cache.py`):
```python
class Cache:
    - Cache-aside pattern (@cache_aside)
    - Configurable TTLs per operation type
    - Backends: PostgresBackend (DATABASE_URL) or MemoryBackend (CACHE_BACKEND=memory)
    - Cache.from_env() never connects; the PostgreSQL pool opens on first use
    - Never raises: backend errors are logged (rate limited), counted, and become misses;
      after a database error the cache backs off for 30 s (is_connected() does no I/O)
    - Statistics tracking
```

### Key Design Patterns

**Lazy Service Initialization:**
```python
# Services initialized on first use
def get_ical_service() -> Optional[MultiCalendarService]:
    global _ical_service
    if _ical_service is None:
        # Initialize from ICAL_FEED_CONFIGS env var
    return _ical_service
```

**Configuration Hot-Reload:**
```python
# server.py:64-68
current_config = os.getenv('ICAL_FEED_CONFIGS', '')
if current_config != _last_config:
    _ical_service = None  # Trigger reinitialization
```

**MCP Tool Registration:**
```python
# Tools registered via @mcp.tool() decorator
@mcp.tool(name="get_events_today", description="...")
def get_events_today() -> Dict[str, Any]:
    service = get_ical_service()
    if not service:
        return {"error": "Service not initialized"}
    # ... implementation
```

## Environment Configuration

### Required

- `ICAL_FEED_CONFIGS`: JSON array of calendar feeds
  ```json
  [{"name":"Work","url":"https://..."},{"name":"Personal","url":"https://..."}]
  ```

### Optional

- `TIMEZONE`: IANA timezone name (default: `UTC`)
- `REFRESH_INTERVAL`: Minutes between refreshes (default: `60`)
- `DEBUG`: Enable debug logging (default: `false`)

### HTTP Mode Specific

- `MCP_API_KEY`: API key for authentication
- `HOST`: Bind address (default: `0.0.0.0`)
- `PORT`: Listen port (default: `80`)

### Cache

- `DATABASE_URL`: PostgreSQL URL; the `cache_entries` table is created on first use (never logged)
- `CACHE_BACKEND`: `memory` for a process-local cache (takes precedence over `DATABASE_URL`)
- `CACHE_TTL_*`: Override default TTLs

## Development Commands

### Running Locally

**HTTP mode:**
```bash
# Unauthenticated (development only)
python server_remote.py

# Authenticated (recommended)
MCP_API_KEY=test-key python server_remote.py
```

### Testing

**Run all tests (coverage floor from pyproject.toml):**
```bash
python -m pytest -q
```

**Run specific test file:**
```bash
python -m pytest tests/test_ical.py -v
```

**Run tests with coverage:**
```bash
python -m pytest tests/ --cov=services --cov-report=term-missing
```

**Run tests by marker:**
```bash
pytest -m ical          # iCalendar tests only
pytest -m unit          # Unit tests only
```

### Docker

```bash
docker-compose up --build
```

## Testing Architecture

Tests use pytest with extensive mocking via `conftest.py`.

### Key Fixtures

```python
# conftest.py
@pytest.fixture
def mock_ical_feeds():
    # Mocked HTTP responses for calendar feeds

@pytest.fixture
def temp_calendar_service(mock_ical_feeds):
    # Temporary service instance for testing
```

### Test Markers

```python
pytestmark = pytest.mark.ical  # Mark entire file
@pytest.mark.unit             # Unit test
```

### Cache Tests

`tests/test_cache.py` runs every behavioural test through the parameterized `harness` fixture:

- `memory`: `Cache(MemoryBackend(clock=FakeClock()))`, so TTL tests advance a fake clock;
- `postgres`: runs only when `TEST_DATABASE_URL` points at a scratch database (the tests
  delete every cache entry in it); skipped otherwise. See README "Running the tests".

All tests avoid making real HTTP requests or requiring live API tokens.

## Implementation Details

### Calendar Service (`services/ical.py`)

**Feed Management:**
- Supports multiple named calendar feeds simultaneously
- Uses `recurring-ical-events` library for RRULE expansion
- Automatic refresh timer (default: 60 minutes)
- Thread-safe with `threading.Lock`
- Each feed has unique ID based on URL hash

**Tool Registration:**
```python
def _register_mcp_tools(self, mcp_instance: FastMCP):
    # Tools are registered dynamically when mcp instance provided
    # This allows service to work standalone or with MCP
```

**Event Parsing:**
- Parse .ics format using `icalendar` library
- Expand recurring events using `recurring-ical-events`
- Normalize timezones to configured TIMEZONE
- Handle all-day events vs timed events

### Authentication (server_remote.py)

**Dual-Factor Path Authentication:**

When `MCP_API_KEY` is set:
1. Hash calculated on startup: `hashlib.sha256(api_key.encode()).hexdigest()`
2. Endpoints mounted at: `/{api_key}/{api_key_hash}/endpoint`
3. Security headers added via middleware
4. Access logs disabled to prevent key leakage

**Endpoints:**
- MCP: `/{api_key}/{api_key_hash}/mcp` (authenticated)
- Health: `/health` (public, no auth)

**Scripts for URL calculation:**
- `scripts/verify_auth.py`: Python script with detailed output
- `scripts/get_mcp_url.sh`: Bash script with clipboard copy

### Caching Strategy

The cache uses the cache-aside pattern with configurable TTLs:

```python
# Default TTLs
CACHE_TTL_CALENDAR_EVENTS = 900   # 15 min (ical:events, ical:search)
CACHE_TTL_CALENDAR_INFO = 1800     # 30 min (ical:info)
```

**Cache Keys:**
```
{service}:{operation}:{identifier}
Examples:
- calendar:events:feed_abc123:2024-01-15
- calendar:info:feed_abc123
```

**Decorator Pattern:**
```python
@cache_aside(ttl=900, key_prefix="calendar:events")
def get_events(...):
    # Implementation
```

## Common Gotchas

### 1. ICAL_FEED_CONFIGS Parsing

The parser handles multiple formats:
- Plain JSON: `[{"name":"Work","url":"https://..."}]`
- Escaped JSON: `[{\"name\":\"Work\",\"url\":\"https://...\"}]`
- Name=JSON format: `Work=[{"url":"https://..."}]`

Always validate JSON structure when modifying parser.

### 2. Service Initialization

Services are NOT initialized at import time:
```python
# This returns None if config missing
service = get_ical_service()
if not service:
    return {"error": "Service not initialized"}
```

Always check for `None` before using service.

### 3. MCP Tool Registration

Tools are registered in two places:
- Service-specific: `ical.py:_register_mcp_tools()`
- Server-level: `server.py:@mcp.tool()` decorators

When adding new tools, ensure proper registration.

### 4. Thread Safety

Calendar service uses background refresh timer:
```python
# Always acquire lock before modifying state
with self._lock:
    self.feeds[feed_id] = new_feed
```

### 5. Docker Deployment

Single `Dockerfile` optimized for HTTP mode with lazy initialization for fast cold starts. See `OPTIMIZATION.md` for details.

## Server-Level Tools

In addition to service-specific tools, these are always available:

**Date/Time:**
- `get_current_datetime`: Returns current date, time, and timezone information

**Server Management:**
- `get_server_status`: Service health and status
- `get_server_config`: Configuration values (non-sensitive)

**Cache Management** (if a cache is configured):
- `get_cache_stats`: Cache performance metrics
- `get_cache_info`: `connected`, `backend`, `database` (host and name only), `server_version`, `entries`, `table_bytes`
- `clear_cache`: Clear cache entries by pattern
- `reset_cache_stats`: Reset cache statistics

## Adding New Features

### 1. Add New Calendar Tool

```python
# In services/ical.py, add to _register_mcp_tools()
@mcp_instance.tool(
    name="my_new_tool",
    description="What this tool does"
)
def my_new_tool_impl(param: str) -> Dict[str, Any]:
    # Implementation
    pass
```

### 2. Add Server-Level Tool

```python
# In server.py
@mcp.tool(
    name="my_server_tool",
    description="What this does"
)
def my_server_tool() -> Dict[str, Any]:
    # Implementation
    pass
```

### 3. Add Environment Variable

```python
# In server.py initialization
new_var = os.getenv('NEW_VAR', 'default_value')

# Update .env.example
# NEW_VAR=default_value
```

### 4. Add Test

```python
# In tests/test_ical.py
@pytest.mark.ical
def test_my_new_feature(temp_calendar_service):
    """Test my new feature"""
    result = temp_calendar_service.my_method()
    assert result["success"] is True
```

## Code Style

- Use type hints for all function parameters and returns
- Document functions with docstrings
- Use descriptive variable names
- Keep functions focused (single responsibility)
- Handle errors gracefully with try/except
- Return consistent response formats (usually `Dict[str, Any]`)

## Error Handling

Standard error response format:
```python
{
    "error": "Error description",
    "details": "Additional context"  # optional
}
```

Always catch exceptions and return error dicts:
```python
try:
    result = operation()
    return {"success": True, "data": result}
except Exception as e:
    logger.error(f"Operation failed: {e}")
    return {"error": str(e)}
```

## Logging

```python
import logging
logger = logging.getLogger(__name__)

# Log levels
logger.debug("Detailed information")    # DEBUG mode only
logger.info("Normal operation")         # Always logged
logger.warning("Warning message")       # Warnings
logger.error("Error message")           # Errors

# Configure via DEBUG env var
DEBUG=true  # Enables debug logging
```

## Dependencies

Key libraries:
- **fastmcp**: FastMCP framework for building MCP servers
- **icalendar**: iCalendar parsing
- **recurring-ical-events**: RRULE expansion for recurring events
- **psycopg** (with `psycopg_pool`): Optional PostgreSQL cache
- **fastapi** / **uvicorn**: HTTP server (server_remote.py only)
- **python-dotenv**: Environment variable management

## Performance Considerations

1. **Calendar Refresh**: Runs in background thread, doesn't block requests
2. **Cache Usage**: Reduces redundant parsing and HTTP requests
3. **Connection Pooling**: The PostgreSQL cache uses a small pool (1-3 connections, 2 s checkout, 3 s connect and statement timeouts)
4. **Event Filtering**: Filter events early to reduce memory usage
5. **Lazy Loading**: Services initialized only when needed

## Security Considerations

1. **API Keys**: Never log full API keys (truncate to first 8 chars)
2. **Feed URLs**: May contain auth tokens, never expose in tool responses
3. **Access Logs**: Disabled in HTTP mode to prevent key leakage
4. **Input Validation**: Validate all user inputs
5. **Error Messages**: Don't expose internal paths or sensitive config

## Debugging Tips

### Check Service Status

```python
# In Python shell
from server import get_ical_service
service = get_ical_service()
if service:
    print(f"Feeds: {len(service.feeds)}")
    for feed_id, feed in service.feeds.items():
        print(f"  {feed.name}: {len(feed.events)} events")
```

### Enable Debug Logging

```bash
DEBUG=true python server.py
```

### Test Calendar Parsing

```python
from icalendar import Calendar
import requests

resp = requests.get("https://your-feed-url.ics")
cal = Calendar.from_ical(resp.content)
for component in cal.walk():
    if component.name == "VEVENT":
        print(component.get('summary'))
```

### Verify the Cache Connection

```python
from src.services.cache import Cache
cache = Cache.from_env()  # reads DATABASE_URL; does not connect yet
cache.set("probe", "ok", ttl=60)
print(cache.get("probe"), cache.info())  # errors count in cache.get_stats().errors
```

## Common Development Workflows

### Add New Calendar Tool

1. Add method to `MultiCalendarService` class
2. Register in `_register_mcp_tools()`
3. Add tests in `tests/test_ical.py`
4. Update AGENTS.md with new tool documentation

### Fix Bug

1. Write failing test that reproduces bug
2. Fix the bug
3. Verify test passes
4. Run full test suite
5. Commit with clear message

### Update Dependencies

1. Update `[project].dependencies` (runtime) or the `dev` extra in `pyproject.toml`
2. Rebuild Docker image
3. Run full test suite
4. Test in Docker container
5. Update documentation if API changes

## File Structure

```
calendar-mcp/
├── server_remote.py          # HTTP API entry point
├── services/
│   ├── __init__.py
│   ├── ical.py              # Calendar service
│   └── cache.py             # Cache service (PostgreSQL / memory)
├── tests/
│   ├── conftest.py          # Pytest fixtures
│   ├── test_ical.py         # Calendar tests
│   └── test_current_datetime.py
├── scripts/
│   ├── verify_auth.py       # Calculate auth URLs
│   └── get_mcp_url.sh       # Bash version
├── Dockerfile               # HTTP mode (optimized)
├── docker-compose.yml       # Docker compose config
├── .env.example             # Environment template
└── pyproject.toml           # Metadata, dependencies, tool config
```

## Useful References

- **FastMCP Docs**: Framework documentation
- **iCalendar RFC 5545**: https://tools.ietf.org/html/rfc5545
- **RRULE Spec**: https://icalendar.org/iCalendar-RFC-5545/3-8-5-3-recurrence-rule.html
- **MCP Protocol**: Model Context Protocol specification
- **psycopg 3**: https://www.psycopg.org/psycopg3/docs/

## Questions or Issues?

When encountering issues:

1. Check logs first (`DEBUG=true`)
2. Verify environment variables are set
3. Check test suite for similar examples
4. Review conftest.py for available fixtures
5. Consult this document for architecture details
