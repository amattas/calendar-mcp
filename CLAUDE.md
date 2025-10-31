# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **Model Context Protocol (MCP) server** for calendar management. The server provides iCalendar feed management with recurring event support and optional Redis caching.

**Core Service:**
- **iCalendar**: Fetches and parses .ics calendar feeds (Google Calendar, Outlook, etc.)

**Deployment:**
- **HTTP mode** (`server_remote.py`): HTTP API with dual-factor authentication

## Quick Reference

**For detailed development documentation**, see [AGENTS.md](AGENTS.md)

**For user documentation**, see [README.md](README.md)

## Architecture

### Main Entry Points
- `server_remote.py` - HTTP API server with authentication

### Service Layer (`services/`)
- `services/ical.py` - Multi-calendar feed management with recurring event support
- `services/cache.py` - Redis caching layer with cache-aside pattern

### Key Design Patterns
- Services initialized lazily via `get_ical_service()`
- Configuration changes trigger service reinitialization (server.py:64-68)
- Tools registered via `@mcp.tool()` decorator
- Optional Redis caching with `@cache_aside` decorator

## Environment Configuration

The server loads from `.env` and `.env.local` (`.env.local` takes precedence).

**Required:**
- `ICAL_FEED_CONFIGS` - JSON array: `[{"name":"Work","url":"https://..."}]`

**Optional:**
- `TIMEZONE` - IANA timezone name (default: "UTC")
- `REFRESH_INTERVAL` - Minutes between refreshes (default: 60)
- `DEBUG` - Enable debug logging (default: false)

**HTTP Mode:**
- `MCP_API_KEY` - API key for authentication (strongly recommended)
- `HOST` - Bind address (default: "0.0.0.0")
- `PORT` - Listen port (default: 80)

**Redis Cache:**
- `REDIS_HOST` / `REDIS_SSL_PORT` / `REDIS_KEY` - Optional caching

**Important:** `ICAL_FEED_CONFIGS` must be valid JSON.

## Development Commands

### Running Locally

**HTTP mode:**
```bash
MCP_API_KEY=test-key python server_remote.py
```

### Testing

```bash
# Run all tests
./run_tests.sh

# Specific test file
python -m pytest tests/test_ical.py -v

# With coverage
python -m pytest tests/ --cov=services --cov-report=term-missing

# By marker
pytest -m ical          # iCalendar tests
pytest -m unit          # Unit tests
```

### Docker

```bash
docker-compose up --build
```

## Testing Architecture

Tests use pytest with extensive mocking via `conftest.py`. Key fixtures:
- `mock_ical_feeds` - Mocked HTTP responses for calendar feeds
- `mock_redis` - Mocked Redis client
- `temp_calendar_service` - Temporary service instance

All tests avoid real HTTP requests or live API tokens.

## Key Implementation Details

### Calendar Service (`services/ical.py`)

- Supports multiple named calendar feeds
- Uses `recurring-ical-events` for RRULE expansion
- Automatic refresh timer (configurable)
- Thread-safe with `threading.Lock`
- Unique feed IDs based on URL hash
- Tools registered via `_register_mcp_tools()` when mcp instance provided

### Authentication (server_remote.py)

When `MCP_API_KEY` is set:
- **Dual-factor path**: `/app/{api_key}/{api_key_hash}/endpoint`
- **MD5 hash calculation**:
  - With salt: `hashlib.md5(f"{MD5_SALT}{api_key}".encode()).hexdigest()`
  - Without salt (legacy): `hashlib.md5(api_key.encode()).hexdigest()`
- **Endpoints**:
  - MCP: `/app/{key}/{hash}/mcp` (authenticated)
  - Health: `/app/health` (public, no auth)
- **Anti-brute-force protection**:
  - Invalid auth paths trigger 30-second delay before 404
  - Failed attempts logged with source IP
  - Health check exempt from delays
- Use `scripts/verify_auth.py` to calculate correct URLs
- Security headers added automatically
- Access logs disabled to prevent key leakage

### Caching Strategy

Redis cache-aside pattern with TTLs:
- Calendar events: 900s (15 min)
- Calendar info: 1800s (30 min)
- Calendar feed: 600s (10 min)

Cache keys: `{service}:{operation}:{identifier}`

## Common Gotchas

1. **ICAL_FEED_CONFIGS parsing**: Complex parser handles multiple formats. Always validate JSON when modifying.

2. **Service initialization**: Services are lazy-loaded. Always check for `None`:
   ```python
   service = get_ical_service()
   if not service:
       return {"error": "Service not initialized"}
   ```

3. **MCP tool registration**:
   - Service tools: `ical.py:_register_mcp_tools()`
   - Server tools: `server.py:@mcp.tool()`

4. **Thread safety**: Calendar service uses background refresh. Always acquire `_lock` before modifying state.

5. **Docker deployment**: Single `Dockerfile` optimized for HTTP mode with fast cold starts.

## Server-Level Tools

Always available:
- `get_current_datetime` - Current date/time in configured timezone
- `get_server_status` - Service health and status
- `get_server_config` - Configuration (non-sensitive)

If Redis configured:
- `get_cache_stats` - Cache performance metrics
- `get_cache_info` - Redis server info
- `clear_cache` - Clear cache by pattern
- `reset_cache_stats` - Reset statistics

## Adding New Features

### New Calendar Tool
1. Add method to `MultiCalendarService`
2. Register in `_register_mcp_tools()`
3. Add tests in `tests/test_ical.py`
4. Update documentation

### New Server Tool
1. Add to `server.py` with `@mcp.tool()` decorator
2. Add tests
3. Update documentation

### New Environment Variable
1. Add to initialization in `server.py`
2. Update `.env.example`
3. Update documentation

## Dependencies

- **fastmcp** - MCP server framework
- **icalendar** - iCalendar parsing
- **recurring-ical-events** - RRULE expansion
- **redis** - Optional caching
- **fastapi** / **uvicorn** - HTTP server (server_remote.py)
- **python-dotenv** - Environment management

## Code Style

- Use type hints for parameters and returns
- Document with docstrings
- Descriptive variable names
- Single responsibility functions
- Graceful error handling with try/except
- Consistent response format: `Dict[str, Any]`

## Error Response Format

```python
{
    "error": "Error description",
    "details": "Additional context"  # optional
}
```

Always catch and return error dicts:
```python
try:
    result = operation()
    return {"success": True, "data": result}
except Exception as e:
    logger.error(f"Failed: {e}")
    return {"error": str(e)}
```

## Logging

```python
import logging
logger = logging.getLogger(__name__)

logger.debug("Detail")     # DEBUG mode only
logger.info("Normal")       # Always
logger.warning("Warning")   # Warnings
logger.error("Error")       # Errors

# Enable via DEBUG env var
DEBUG=true
```

## Security Notes

1. **API Keys**: Never log full keys (truncate to 8 chars)
2. **MD5 Salt**: `MD5_SALT` environment variable adds extra security layer to hash
3. **Feed URLs**: May contain auth tokens, never expose
4. **Access Logs**: Disabled in HTTP mode to prevent key leakage
5. **Anti-Brute-Force**: 30-second delay on failed auth attempts (server_remote.py:157-162)
6. **Input Validation**: Validate all user inputs
7. **Error Messages**: Don't expose internal paths
8. **Rate Limiting**: Consider IP-based limiting at reverse proxy level

## Debugging Tips

```python
# Check service status
from server import get_ical_service
service = get_ical_service()
if service:
    print(f"Feeds: {len(service.feeds)}")

# Enable debug logging
DEBUG=true python server.py

# Test calendar parsing
from icalendar import Calendar
import requests
resp = requests.get("https://feed-url.ics")
cal = Calendar.from_ical(resp.content)
```

## File Structure

```
calendar-mcp/
├── server.py                 # stdio entry
├── server_remote.py          # HTTP entry
├── services/
│   ├── ical.py              # Calendar service
│   └── cache.py             # Redis cache
├── tests/
│   ├── conftest.py          # Fixtures
│   └── test_ical.py         # Tests
├── scripts/
│   └── verify_auth.py       # Auth URL calculator
├── Dockerfile               # HTTP mode (optimized)
├── docker-compose.yml       # Docker compose config
├── .env.example             # Config template
└── requirements.txt         # Dependencies
```

## Documentation

- **README.md** - User documentation and deployment guides
- **AGENTS.md** - Detailed development guide for AI agents
- **CLAUDE.md** - This file, Claude Code specific guidance
- `.env.example` - Environment variable documentation

## Useful References

- **iCalendar RFC 5545**: https://tools.ietf.org/html/rfc5545
- **RRULE Spec**: https://icalendar.org/iCalendar-RFC-5545/3-8-5-3-recurrence-rule.html
- **FastMCP**: Framework documentation
- **MCP Protocol**: Model Context Protocol specification

When in doubt, consult:
1. This file for quick reference
2. AGENTS.md for implementation details
3. README.md for deployment information
4. Test suite for usage examples
