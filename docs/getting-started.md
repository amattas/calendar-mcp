# Getting Started

This guide shows how to run the Calendar MCP server and connect it to MCP clients.

## Quick Start with Docker

For production HTTP access:

```bash
cp .env.example .env.local
# Edit .env.local: set ICAL_FEED_CONFIGS and MCP_API_KEY

docker-compose up --build
```

After the container is running, you can calculate your MCP endpoint URLs:

```bash
python scripts/verify_auth.py --api-key YOUR_KEY --domain localhost:8080 --no-https
```

Key endpoints:

- Health: `http://localhost:8080/app/health`
- MCP: `http://localhost:8080/app/{API_KEY}/{API_KEY_HASH}/mcp`

## Local Development (without Docker)

1. Create a virtual environment:

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Set environment variables (at minimum `ICAL_FEED_CONFIGS`):

   ```bash
   export ICAL_FEED_CONFIGS='[{"name":"Work","url":"https://..."}]'
   ```

4. Run the MCP server (stdio transport):

   ```bash
   python src/server.py
   ```

For HTTP mode, use `src/server_remote.py` instead and configure `MCP_API_KEY`, `HOST`, and `PORT`.
