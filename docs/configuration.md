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

## Redis Cache (Optional)

If you enable Redis caching via `RedisCache`:

- `REDIS_HOST` – Redis hostname
- `REDIS_SSL_PORT` – Redis SSL port (default: `6380`)
- `REDIS_KEY` – Redis access key

When configured, the calendar service will use Redis for caching parsed events and other data to improve performance.
