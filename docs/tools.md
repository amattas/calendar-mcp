# MCP Tools

The Calendar MCP server exposes a set of MCP tools for working with calendar data and cache management.

High-level categories include:

- **Calendar queries** – Get events for today, upcoming events, and date/range-based queries
- **Service status** – Inspect server and calendar service status
- **Configuration** – View non-sensitive configuration values
- **Cache management** – Inspect and manage Redis cache statistics and contents

Refer to:

- `src/server.py` for server-level tools such as:
  - `get_current_datetime`
  - `get_server_status`
  - `get_server_config`
  - `get_cache_stats`
  - `clear_cache`
  - `get_cache_info`
  - `reset_cache_stats`
- `src/services/ical.py` for calendar-specific operations implemented by `MultiCalendarService`

For detailed design notes intended for AI agents and contributors, see `AGENTS.md` in the repository root.
