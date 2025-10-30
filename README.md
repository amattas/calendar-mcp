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
- **Multiple Deployment Modes**: stdio (Claude Desktop), HTTP (remote), and Kubernetes

## Quick Start

### Local Development (stdio mode)

Perfect for Claude Desktop integration:

```bash
# 1. Clone and configure
cp .env.example .env.local
# Edit .env.local with your calendar feeds

# 2. Run with Docker Compose
docker-compose up --build

# 3. Configure Claude Desktop
# Add to ~/Library/Application Support/Claude/claude_desktop_config.json (macOS)
# or %APPDATA%\Claude\claude_desktop_config.json (Windows)
```

See [claude_desktop_config.json.example](claude_desktop_config.json.example) for configuration details.

### HTTP Server Mode

For remote access and testing before Kubernetes deployment:

```bash
# 1. Configure environment
cp .env.example .env.local
# Edit .env.local: set ICAL_FEED_CONFIGS and MCP_API_KEY

# 2. Run with Docker Compose
docker-compose -f docker-compose.http.yml up --build

# 3. Calculate your endpoint URLs
python scripts/verify_auth.py --api-key YOUR_KEY --domain localhost:8080 --no-https

# 4. Access endpoints
# Health: http://localhost:8080/{API_KEY}/{MD5_HASH}/health/
# MCP: http://localhost:8080/{API_KEY}/{MD5_HASH}/mcp
```

### Kubernetes Deployment

Production deployment with ingress controller:

```bash
# 1. Build and push image
docker build -f Dockerfile.http -t your-registry/calendar-mcp:latest .
docker push your-registry/calendar-mcp:latest

# 2. Configure secrets
cp .env.k8s.example .env.k8s
# Edit .env.k8s with your credentials
kubectl create secret generic calendar-mcp-secrets --from-env-file=.env.k8s

# 3. Update deployment manifest
# Edit k8s-deployment.yaml: update image and ingress host

# 4. Deploy
kubectl apply -f k8s-deployment.yaml

# 5. Calculate endpoint URLs
export MCP_API_KEY=$(kubectl get secret calendar-mcp-secrets -o jsonpath='{.data.MCP_API_KEY}' | base64 -d)
python scripts/verify_auth.py --api-key "$MCP_API_KEY" --domain your-domain.com
```

See [Kubernetes Deployment](#kubernetes-deployment) section for complete guide.

## Configuration

### Environment Variables

**Required:**
- `ICAL_FEED_CONFIGS` - JSON array of calendar feeds

**Optional:**
- `TIMEZONE` - IANA timezone name (default: `UTC`)
- `REFRESH_INTERVAL` - Minutes between refreshes (default: `60`)
- `DEBUG` - Enable debug logging (default: `false`)

**HTTP/Kubernetes Mode:**
- `MCP_API_KEY` - API key for authentication (strongly recommended)
- `HOST` - Bind address (default: `0.0.0.0`)
- `PORT` - Listen port (default: `8080`)

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

## Deployment Modes

### 1. stdio Mode (Claude Desktop)

```bash
docker-compose up --build
```

**Use case:** Local Claude Desktop integration
**Entry point:** `server.py`
**Communication:** stdin/stdout

### 2. HTTP Server Mode

```bash
docker-compose -f docker-compose.http.yml up --build
```

**Use case:** Remote HTTP access, testing
**Entry point:** `server_remote.py`
**Port:** 8080
**Authentication:** Dual-factor path (API key + MD5 hash)

### 3. Kubernetes Mode

```bash
kubectl apply -f k8s-deployment.yaml
```

**Use case:** Production deployment
**Image:** `Dockerfile.http`
**Port:** 8080
**Features:** Replicas, health checks, ingress, autoscaling

## Authentication (HTTP/Kubernetes Mode)

The server uses **dual-factor path authentication** requiring both the API key and its MD5 hash:

```
https://your-domain.com/{API_KEY}/{MD5_HASH}/endpoint
```

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
api_key_hash = hashlib.md5(api_key.encode()).hexdigest()
print(f"https://domain.com/{api_key}/{api_key_hash}/mcp")
```

### Available Endpoints

- **Root** (`/`): Server info (public, no auth)
- **Health** (`/{KEY}/{HASH}/health/`): Health check
- **Info** (`/{KEY}/{HASH}/info/`): Available tools and services
- **MCP** (`/{KEY}/{HASH}/mcp`): MCP protocol endpoint

### Security Benefits

1. **Two-Factor Protection**: Requires both API key and hash
2. **Hash Not Stored**: MD5 hash calculated at runtime
3. **Path Obfuscation**: Even with one factor, endpoint is inaccessible
4. **No Token Exchange**: Stateless authentication

## Kubernetes Deployment

### Prerequisites

- Kubernetes cluster running
- `kubectl` configured
- Container registry access
- Ingress controller installed (nginx, traefik, etc.)

### Step-by-Step Deployment

**1. Generate Strong API Key:**
```bash
openssl rand -base64 32
```

**2. Create Kubernetes Secret:**
```bash
cp .env.k8s.example .env.k8s
# Edit .env.k8s with your configuration

kubectl create secret generic calendar-mcp-secrets \
  --from-env-file=.env.k8s \
  --namespace=default
```

**3. Update Deployment Manifest:**

Edit `k8s-deployment.yaml`:
- Update `image:` with your registry URL
- Update `host:` in ingress with your domain

**4. Deploy:**
```bash
kubectl apply -f k8s-deployment.yaml
```

**5. Verify:**
```bash
# Check pods
kubectl get pods -l app=calendar-mcp

# Check ingress
kubectl get ingress calendar-mcp

# View logs
kubectl logs -l app=calendar-mcp --tail=50 -f
```

**6. Get Endpoint URLs:**
```bash
export MCP_API_KEY=$(kubectl get secret calendar-mcp-secrets \
  -o jsonpath='{.data.MCP_API_KEY}' | base64 -d)

python scripts/verify_auth.py \
  --api-key "$MCP_API_KEY" \
  --domain your-domain.com
```

### SSL/TLS Configuration

**Using cert-manager (recommended):**

```bash
# Install cert-manager
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.13.0/cert-manager.yaml

# Create ClusterIssuer
kubectl apply -f - <<EOF
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: your-email@example.com
    privateKeySecretRef:
      name: letsencrypt-prod
    solvers:
    - http01:
        ingress:
          class: nginx
EOF

# Uncomment TLS section in k8s-deployment.yaml
```

### Scaling

**Manual:**
```bash
kubectl scale deployment calendar-mcp --replicas=5
```

**Auto-scaling:**
```bash
kubectl autoscale deployment calendar-mcp \
  --min=2 --max=10 --cpu-percent=70
```

### Monitoring

```bash
# Logs
kubectl logs -l app=calendar-mcp --tail=100 -f

# Resource usage
kubectl top pods -l app=calendar-mcp

# Events
kubectl get events --sort-by='.lastTimestamp'
```

### Troubleshooting

**Pods not starting:**
```bash
kubectl describe pod calendar-mcp-xxxxxxxxx-xxxxx
kubectl get events --sort-by='.lastTimestamp'
```

**Connection issues:**
```bash
# Port forward to test directly
kubectl port-forward svc/calendar-mcp 8080:8080
curl http://localhost:8080/
```

**Configuration issues:**
```bash
# Check ConfigMap and Secret
kubectl get configmap calendar-mcp-config -o yaml
kubectl get secret calendar-mcp-secrets -o yaml

# Update and restart
kubectl edit configmap calendar-mcp-config
kubectl rollout restart deployment calendar-mcp
```

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
- `add_calendar_feed` - Add a new calendar feed
- `remove_calendar_feed` - Remove a calendar feed
- `refresh_calendar_feeds` - Manually refresh all feeds
- `get_calendar_info` - Get information about configured feeds
- `get_calendar_feeds` - List all configured feeds

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
- ✅ Store in Kubernetes Secrets
- ✅ Use external secret management (Vault, AWS Secrets Manager)
- ✅ Encrypt secrets at rest
- ❌ Never commit to version control
- ❌ Never log API keys

**4. Network Security:**
```yaml
# Network Policy example
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: calendar-mcp-netpol
spec:
  podSelector:
    matchLabels:
      app: calendar-mcp
  policyTypes:
  - Ingress
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          name: ingress-nginx
    ports:
    - protocol: TCP
      port: 8080
```

**5. Rate Limiting:**
```yaml
# Ingress annotation
nginx.ingress.kubernetes.io/limit-rps: "10"
```

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
# 1. Generate new key
NEW_KEY=$(openssl rand -base64 32)

# 2. Update secret
kubectl create secret generic calendar-mcp-secrets \
  --from-literal=MCP_API_KEY="$NEW_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -

# 3. Restart pods
kubectl rollout restart deployment calendar-mcp

# 4. Get new endpoint URLs
python scripts/verify_auth.py --api-key "$NEW_KEY" --domain your-domain.com

# 5. Update MCP clients with new URLs
```

## Production Checklist

- [ ] Strong MCP_API_KEY generated (32+ bytes)
- [ ] HTTPS/TLS configured
- [ ] SSL redirect enabled
- [ ] Certificate auto-renewal configured
- [ ] Network policies applied
- [ ] Rate limiting configured
- [ ] Secrets encrypted at rest
- [ ] RBAC configured for secret access
- [ ] Monitoring and alerting configured
- [ ] Key rotation schedule established
- [ ] Backup and recovery tested
- [ ] Security scanning enabled
- [ ] Pod Security Standards enforced

## Testing

### Test with Claude Desktop

> "What's the status of my calendar server?"
> "What events do I have today?"
> "Show me my schedule for next week"
> "Do I have any scheduling conflicts this month?"

### Test HTTP Endpoints

```bash
# Calculate endpoint URLs
python scripts/verify_auth.py --api-key "$MCP_API_KEY" --domain localhost:8080 --no-https

# Test health check
curl http://localhost:8080/$API_KEY/$API_HASH/health/

# Test server info
curl http://localhost:8080/$API_KEY/$API_HASH/info/
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

### 502 Bad Gateway (Kubernetes)
```bash
# Check pods are ready
kubectl get pods -l app=calendar-mcp

# Check logs for errors
kubectl logs -l app=calendar-mcp --tail=50

# Common causes:
# - Missing ICAL_FEED_CONFIGS
# - Invalid JSON in ICAL_FEED_CONFIGS
# - Missing MCP_API_KEY
```

## Architecture

```
Claude Desktop / MCP Client
     ↓ (stdio or HTTP)
Docker Container
     ↓
MCP Server (FastMCP)
     ├─ server.py (stdio mode)
     └─ server_remote.py (HTTP mode)
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
