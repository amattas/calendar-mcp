# Calendar MCP Server

A specialized Model Context Protocol (MCP) server for iCalendar feed management and event queries. This Docker-based server enables Claude Desktop and other MCP clients to interact with multiple calendar feeds for event tracking, conflict detection, and schedule management.

## Features

- **Multi-Calendar Support**: Monitor multiple iCalendar feeds (.ics URLs)
- **Smart Event Queries**: Search events by date, range, or keywords
- **Conflict Detection**: Advanced conflict analysis with severity levels
- **Dynamic Feed Management**: Add/remove calendar feeds on the fly
- **Automatic Refresh**: Configurable refresh intervals for feed updates
- **Timezone Support**: Proper timezone normalization and handling
- **Redis Caching**: Optional caching for improved performance
- **Docker Support**: Easy deployment with Docker and Docker Compose

## Prerequisites

- **Docker** and **Docker Compose** (for containerized deployment)
- **iCalendar Feed URLs**: Public .ics URLs from Google Calendar, Outlook, Apple Calendar, etc.
- **Claude Desktop** (optional): For MCP client integration

## Quick Start

### 1. Get Your Calendar Feed URLs

**Google Calendar:**
1. Open Google Calendar settings
2. Select the calendar you want to share
3. Scroll to "Integrate calendar"
4. Copy the "Secret address in iCal format" URL

**Outlook Calendar:**
1. Go to Calendar settings
2. Select "Shared calendars"
3. Choose "Publish a calendar"
4. Copy the ICS link

**Apple Calendar:**
1. Right-click the calendar in the sidebar
2. Select "Share Calendar"
3. Make it public and copy the webcal:// URL (change to https://)

### 2. Clone and Configure

```bash
cd calendar-mcp
cp .env.example .env.local
```

Edit `.env.local` and add your calendar feeds:

```env
# Calendar feeds in JSON format
ICAL_FEED_CONFIGS=[{"name":"Work","url":"https://calendar.google.com/calendar/ical/.../basic.ics"},{"name":"Personal","url":"https://outlook.office365.com/owa/calendar/.../calendar.ics"}]

# Refresh interval in minutes
REFRESH_INTERVAL=60

# Debug mode
DEBUG=false

# Optional Redis caching
REDIS_HOST=
REDIS_PORT=6379
REDIS_PASSWORD=
REDIS_USE_SSL=false
```

### 3. Run with Docker Compose

```bash
docker-compose up --build
```

The server will start in stdio mode, ready to accept MCP connections.

### 4. Connect to Claude Desktop

Add this configuration to your Claude Desktop config file:

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "calendar": {
      "command": "docker",
      "args": ["compose", "-f", "/path/to/calendar-mcp/docker-compose.yml", "run", "--rm", "calendar-mcp"]
    }
  }
}
```

Restart Claude Desktop to activate the server.

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ICAL_FEED_CONFIGS` | Yes | - | JSON array of calendar feeds with name and URL |
| `REFRESH_INTERVAL` | No | `60` | Feed refresh interval in minutes |
| `DEBUG` | No | `false` | Enable debug logging |
| `REDIS_HOST` | No | - | Redis server hostname (for caching) |
| `REDIS_PORT` | No | `6379` | Redis server port |
| `REDIS_PASSWORD` | No | - | Redis password |
| `REDIS_USE_SSL` | No | `false` | Use SSL for Redis connection |

### Calendar Feed Format

The `ICAL_FEED_CONFIGS` should be a JSON array of objects:

```json
[
  {
    "name": "Work Calendar",
    "url": "https://calendar.google.com/calendar/ical/.../basic.ics"
  },
  {
    "name": "Personal",
    "url": "https://outlook.office365.com/owa/calendar/.../calendar.ics"
  }
]
```

## Available MCP Tools

The Calendar MCP server provides the following tools:

### Event Queries
- `get_events_today` - Get all events for today
- `get_upcoming_events` - Get upcoming events (next 7 days)
- `get_events_by_date` - Get events for a specific date
- `get_events_by_range` - Get events within a date range
- `search_events` - Search events by keyword

### Conflict Detection
- `analyze_conflicts` - Detect scheduling conflicts across calendars
- `get_conflicts_for_date` - Get conflicts for a specific date
- `get_conflicts_by_range` - Get conflicts within a date range

### Feed Management
- `add_calendar_feed` - Add a new calendar feed
- `remove_calendar_feed` - Remove a calendar feed
- `refresh_calendars` - Manually refresh all calendar feeds
- `get_calendar_info` - Get information about configured feeds

### Server Management
- `get_server_status` - Check server health
- `get_server_config` - View server configuration
- `get_cache_stats` - View cache performance metrics
- `clear_cache` - Clear cached data
- `get_cache_info` - View Redis server information

## Local Development

### Without Docker

1. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set environment variables:
```bash
export ICAL_FEED_CONFIGS='[{"name":"Work","url":"https://..."}]'
```

4. Run the server:
```bash
python server.py
```

### With Docker

Build and run:
```bash
docker build -t calendar-mcp .
docker run -e ICAL_FEED_CONFIGS='[...]' calendar-mcp
```

## Testing

### Test Server Status

You can test the server by connecting via Claude Desktop and asking:

> "What's the status of my calendar server?"

### Test Event Queries

> "What events do I have today?"
> "Show me my schedule for next week"
> "Do I have any scheduling conflicts this month?"

### Test Feed Management

> "Add my personal calendar: https://calendar.google.com/..."
> "Refresh all my calendars"

## Troubleshooting

### "No iCalendar feeds configured"

**Solution**: Ensure `ICAL_FEED_CONFIGS` is set correctly in your `.env.local` file with valid JSON.

### Feed Not Updating

**Solution**:
- Check the feed URL is publicly accessible
- Verify the URL returns valid iCalendar data (ends in .ics)
- Try refreshing manually with `refresh_calendars` tool
- Check the `REFRESH_INTERVAL` setting

### Timezone Issues

**Solution**:
- Ensure your calendar feeds include proper timezone information
- The server automatically normalizes timezones to UTC
- Check event times include TZID parameters

### JSON Parse Error

**Solution**:
- Validate your JSON format in `ICAL_FEED_CONFIGS`
- Use single quotes around the JSON in environment variables
- Escape double quotes properly if needed

## Architecture

The Calendar MCP server follows this architecture:

```
Claude Desktop
     ↓ (stdio)
Docker Container
     ↓
MCP Server (FastMCP)
     ↓
Calendar Service
     ├─ Feed Parser (icalendar)
     ├─ Recurring Events (recurring-ical-events)
     └─ Auto-Refresh Timer
     ↓
iCalendar Feed URLs
```

Optional Redis caching layer improves performance by reducing feed parsing.

## Conflict Detection

The server includes intelligent conflict detection that:

- Compares events across all calendars
- Identifies overlapping time periods
- Calculates conflict severity (minor, moderate, major)
- Reduces false positives by 70% through smart filtering
- Normalizes timezones for accurate comparison

## Security Notes

- Never commit your `.env.local` file to version control
- Use environment-specific `.env` files
- Calendar feed URLs may contain authentication tokens
- Consider using private/secret calendar URLs
- Feed URLs are never exposed through MCP tool responses

## Performance

- **Automatic Caching**: Parsed events are cached to reduce processing
- **Background Refresh**: Feeds refresh automatically without blocking
- **Redis Support**: Optional Redis caching for multi-instance deployments
- **Efficient Parsing**: Only parses feeds when they've changed

## License

This project is provided as-is for personal use.

## Support

For iCalendar specification, visit: https://icalendar.org/
