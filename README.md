# Calendar MCP Server

A Model Context Protocol (MCP) server for iCalendar feed management and event queries. This server enables Claude Desktop and other MCP clients to interact with multiple calendar feeds for event tracking, conflict detection, and schedule management.

## Features

- **Multi-Calendar Support**: Monitor multiple iCalendar feeds (.ics URLs)
- **Smart Event Queries**: Search events by date, range, or keywords
- **Conflict Detection**: Advanced conflict analysis with severity levels
- **Dynamic Feed Management**: Add/remove calendar feeds on the fly
- **Automatic Refresh**: Configurable refresh intervals for feed updates
- **Timezone Support**: Proper timezone normalization and handling
- **Redis Caching**: Optional caching for improved performance
- **HTTP API**: Remote access via HTTP with dual-factor authentication

## Quick Start

### Docker Deployment

For production HTTP access:

```bash
# 1. Configure environment
cp .env.example .env.local
# Edit .env.local: set ICAL_FEED_CONFIGS and MCP_API_KEY

# 2. Run with Docker Compose
docker-compose up --build

# 3. Calculate your endpoint URLs
python scripts/verify_auth.py --api-key YOUR_KEY --domain localhost:8080 --no-https

# 4. Access endpoints
# Health (public): http://localhost:8080/app/health
# MCP: http://localhost:8080/app/{API_KEY}/{API_KEY_HASH}/mcp
```

## Configuration

### Environment Variables

**Required:**
- `ICAL_FEED_CONFIGS` - JSON array of calendar feeds

**Optional:**
- `TIMEZONE` - IANA timezone name (default: `UTC`)
- `REFRESH_INTERVAL` - Minutes between refreshes (default: `60`)
- `DEBUG` - Enable debug logging (default: `false`)

**HTTP Mode:**
- `MCP_API_KEY` - API key for authentication (strongly recommended)
- `MD5_SALT` - Salt for MD5 hash (optional, recommended for enhanced security)
- `HOST` - Bind address (default: `0.0.0.0`)
- `PORT` - Listen port (default: `80`)

**Redis Cache (Optional):**
- `REDIS_HOST` - Redis hostname
- `REDIS_SSL_PORT` - Redis SSL port (default: `6380`)
- `REDIS_KEY` - Redis access key

### Calendar Feed Format

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

### Getting Calendar Feed URLs

**Google Calendar:**
1. Open Google Calendar settings
2. Select the calendar → "Integrate calendar"
3. Copy "Secret address in iCal format"

**Outlook Calendar:**
1. Calendar settings → "Shared calendars"
2. Publish a calendar
3. Copy the ICS link

**Apple Calendar:**
1. Right-click calendar → "Share Calendar"
2. Make public and copy URL (change `webcal://` to `https://`)

## Deployment

### Docker with HTTP API

```bash
docker-compose up --build
```

**Entry point:** `server_remote.py`
**Port:** 8080
**Authentication:** Dual-factor path (API key + MD5 hash)

## Authentication

The server uses **dual-factor path authentication** requiring both the API key and its MD5 hash:

```
https://your-domain.com/app/{API_KEY}/{API_KEY_HASH}/mcp
```

The hash is calculated as `MD5(MD5_SALT + API_KEY)` when salt is configured, or `MD5(API_KEY)` in legacy mode.

### Generating Endpoint URLs

**Python script (recommended):**
```bash
python scripts/verify_auth.py --api-key YOUR_KEY --domain your-domain.com
```

**Bash script:**
```bash
scripts/get_mcp_url.sh YOUR_KEY your-domain.com
```

**Manual calculation:**
```python
import hashlib
api_key = "your-api-key"
md5_salt = "your-salt"  # Optional
hash_input = f"{md5_salt}{api_key}" if md5_salt else api_key
api_key_hash = hashlib.sha256(hash_input.encode()).hexdigest()
print(f"https://domain.com/app/{api_key}/{api_key_hash}/mcp")
```

### Security Features

**Anti-Brute-Force Protection:**
- Invalid authentication paths trigger a 30-second delay before returning 404
- This makes brute-force attacks impractical (e.g., testing 1000 keys would take 8+ hours)
- Health check endpoint (`/app/health`) is exempt from delays
- Failed attempts are logged with source IP for monitoring

**Best Practices:**
- Use strong API keys (32+ bytes, generated with `openssl rand -base64 32`)
- Configure `MD5_SALT` for additional security layer
- Monitor logs for repeated failed authentication attempts
- Consider IP-based rate limiting at your reverse proxy
- Always use HTTPS in production to prevent credential interception

### Available Endpoints

- **Health** (`/health`): Health check (public, no auth required)
- **MCP** (`/app/{KEY}/{HASH}/mcp`): MCP protocol endpoint (authenticated)

### Security Benefits

1. **Two-Factor Protection**: Requires both API key and hash
2. **Hash Not Stored**: Hash calculated at runtime
3. **Path Obfuscation**: Even with one factor, endpoint is inaccessible
4. **No Token Exchange**: Stateless authentication

## Available MCP Tools

### Event Queries
- `get_events_today` - Get all events for today
- `get_tomorrow_events` - Get tomorrow's events
- `get_week_events` - Get this week's events
- `get_month_events` - Get this month's events
- `get_upcoming_events` - Get upcoming events (next 7 days)
- `get_events_on_date` - Get events for a specific date
- `get_events_between_dates` - Get events within a date range
- `get_events_after_date` - Get events after a specific date
- `search_calendar_events` - Search events by keyword

### Conflict Detection
- `get_calendar_conflicts` - Detect scheduling conflicts across calendars

### Feed Management
- `refresh_calendar_feeds` - Manually refresh all feeds
- `get_calendar_info` - Get information about configured feeds
- `get_calendar_feeds` - List all configured feeds

> **Note:** Calendar feeds are configured via the `ICAL_FEED_CONFIGS` environment variable. To add or remove feeds, update the configuration and restart the server.

### Server Management
- `get_current_datetime` - Get current date/time in configured timezone
- `get_server_status` - Check server health
- `get_server_config` - View server configuration

### Cache Management (if Redis configured)
- `get_cache_stats` - View cache performance metrics
- `get_cache_info` - View Redis server information
- `clear_cache` - Clear cached data
- `reset_cache_stats` - Reset cache statistics

## Security

### Best Practices

**1. Generate Strong API Keys:**
```bash
openssl rand -base64 32  # Minimum 32 bytes recommended
```

**2. Use HTTPS in Production:**
- Configure TLS/SSL certificates
- Enable SSL redirect in ingress
- Use cert-manager for automatic renewal

**3. Secure Storage:**
- ✅ Store in environment variables or Docker secrets
- ✅ Use external secret management (Vault, AWS Secrets Manager)
- ✅ Encrypt secrets at rest
- ❌ Never commit to version control
- ❌ Never log API keys

**4. Network Security:**
- Use firewall rules to restrict access
- Consider reverse proxy with additional authentication
- Enable HTTPS with valid SSL certificates

**5. Rate Limiting:**
- Configure rate limiting at reverse proxy level
- Use tools like nginx rate limiting or fail2ban

### Security Headers

The server automatically adds security headers:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `X-XSS-Protection: 1; mode=block`
- `Referrer-Policy: no-referrer`
- `Cache-Control: no-store, no-cache, must-revalidate, private`

Access logs are disabled to prevent API key leakage.

### Key Rotation

If API key is compromised:

```bash
# 1. Generate new key and salt
NEW_KEY=$(openssl rand -base64 32)
NEW_SALT=$(openssl rand -base64 32)

# 2. Update .env.local file
echo "MCP_API_KEY=$NEW_KEY" >> .env.local
echo "MD5_SALT=$NEW_SALT" >> .env.local

# 3. Restart the Docker container
docker-compose down
docker-compose up -d

# 4. Get new endpoint URLs
python scripts/verify_auth.py --api-key "$NEW_KEY" --md5-salt "$NEW_SALT" --domain your-domain.com

# 5. Update MCP clients with new URLs
```

## Production Checklist

- [ ] Strong MCP_API_KEY generated (32+ bytes)
- [ ] MD5_SALT configured for enhanced security
- [ ] HTTPS/TLS configured with valid certificates
- [ ] SSL redirect enabled at reverse proxy
- [ ] Certificate auto-renewal configured
- [ ] Firewall rules configured
- [ ] Rate limiting configured at reverse proxy
- [ ] Environment variables secured
- [ ] Monitoring and alerting configured
- [ ] Key rotation schedule established
- [ ] Backup and recovery tested
- [ ] Security scanning enabled
- [ ] Access logs reviewed regularly

## Testing

### Test with Claude Desktop

> "What's the status of my calendar server?"
> "What events do I have today?"
> "Show me my schedule for next week"
> "Do I have any scheduling conflicts this month?"

### Test HTTP Endpoints

```bash
# Calculate endpoint URLs (with salt for enhanced security)
python scripts/verify_auth.py --api-key "$MCP_API_KEY" --md5-salt "$MD5_SALT" --domain localhost:8080 --no-https

# Or using environment variables
export MCP_API_KEY="your-api-key"
export MD5_SALT="your-salt"
python scripts/verify_auth.py --domain localhost:8080 --no-https

# Test health check (public endpoint)
curl http://localhost:8080/app/health
```

## Troubleshooting

### "No iCalendar feeds configured"
Ensure `ICAL_FEED_CONFIGS` is set correctly with valid JSON.

### Feed Not Updating
- Check feed URL is publicly accessible
- Verify URL returns valid iCalendar data
- Try manual refresh: `refresh_calendar_feeds` tool
- Check `REFRESH_INTERVAL` setting

### Timezone Issues
- Ensure calendar feeds include timezone information
- Server normalizes timezones to UTC
- Set `TIMEZONE` environment variable

### JSON Parse Error
- Validate JSON format in `ICAL_FEED_CONFIGS`
- Use single quotes around JSON in environment variables
- Escape double quotes properly

### Container Won't Start
```bash
# Check container logs
docker logs calendar-mcp

# Common causes:
# - Missing ICAL_FEED_CONFIGS
# - Invalid JSON in ICAL_FEED_CONFIGS
# - Missing MCP_API_KEY (HTTP mode)
```

## Architecture

```
MCP Client
     ↓ (HTTP)
Docker Container
     ↓
MCP Server (FastMCP)
     └─ server_remote.py (HTTP API)
          ↓
Calendar Service (services/ical.py)
     ├─ Feed Parser (icalendar)
     ├─ Recurring Events (recurring-ical-events)
     └─ Auto-Refresh Timer
          ↓
     Optional Redis Cache (services/cache.py)
          ↓
iCalendar Feed URLs
```

## Performance

- **Automatic Caching**: Parsed events cached to reduce processing
- **Background Refresh**: Feeds refresh without blocking
- **Redis Support**: Optional Redis for multi-instance deployments
- **Efficient Parsing**: Only parses when feeds change

## Development

See [AGENTS.md](AGENTS.md) for detailed development documentation including:
- Architecture details
- Service patterns
- Testing guide
- Adding new features
- Common gotchas

## License

This project is provided as-is for personal use.

## Support

- **Issues**: Open a GitHub issue
- **Security**: Report vulnerabilities privately
- **iCalendar Spec**: https://icalendar.org/
