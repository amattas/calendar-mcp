# Configuration

The Calendar MCP server is configured primarily via environment variables.

## Core Settings

- `ICAL_FEED_CONFIGS` (required) – JSON array of calendar feeds:

  ```json
  [
    {
      "name": "Work",
      "url": "https://calendar.google.com/calendar/ical/YOUR_ID/basic.ics"
    },
    {
      "name": "Personal",
      "url": "https://outlook.office365.com/owa/calendar/YOUR_ID/calendar.ics"
    }
  ]
  ```

- `TIMEZONE` – IANA timezone name (default: `UTC`)
- `REFRESH_INTERVAL` – Minutes between refreshes (default: `60`)
- `DEBUG` – Enable debug logging (`true` / `false`, default: `false`)

## HTTP Mode

Used by `src/server_remote.py` for remote access:

- `MCP_API_KEY` – API key for authentication (strongly recommended)
- `MD5_SALT` – Salt for MD5 hash (optional, recommended)
- `HOST` – Bind address (default: `0.0.0.0`)
- `PORT` – Listen port (default: `80`)

The HTTP MCP endpoint has the form:

```text
https://your-domain.com/app/{API_KEY}/{API_KEY_HASH}/mcp
```

where `API_KEY_HASH` is calculated as:

- `MD5(MD5_SALT + API_KEY)` when `MD5_SALT` is set, or
- `MD5(API_KEY)` in legacy mode.

## Cache (Optional)

- `DATABASE_URL` – PostgreSQL URL for the response cache
- `CACHE_BACKEND` – `memory` for a process-local cache (local development)

The cache is optional. `CACHE_BACKEND=memory` selects a process-local cache; otherwise a set
`DATABASE_URL` selects PostgreSQL; with neither, every request parses the calendar feeds directly.

```env
DATABASE_URL=postgresql://user:password@host:5432/calendar_mcp?sslmode=require&application_name=calendar-mcp
```

The PostgreSQL backend:

- connects lazily on first use (startup never waits for the database);
- creates its table, `cache_entries` (an `UNLOGGED` table: a database crash may empty it), on first
  connect. The database user needs `CREATE` on schema `public`; if it lacks it, the server logs a
  warning naming the fix (`GRANT ALL ON SCHEMA public TO <user>`, run as `postgres`);
- never fails a request: after any database error it logs (at most once a minute per operation),
  serves misses and reads the calendar feeds directly for 30 seconds, then retries.

`clear_cache` takes a glob: `*` matches any run of characters and `?` one character; character
classes (`[abc]`) are not supported. `get_cache_info` reports the database host and name, never
credentials.

### Cache TTLs

Override with `CACHE_TTL_<NAME>` (seconds):

| Name              | Default | Data                                                  |
|-------------------|---------|-------------------------------------------------------|
| `CALENDAR_EVENTS` | `900`   | Event queries and search results (`ical:events:*`, `ical:search:*`) |
| `CALENDAR_INFO`   | `1800`  | Calendar feed information (`ical:info:*`)             |
