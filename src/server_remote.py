#!/usr/bin/env python3
"""
MattasMCP Remote Server - exposes HTTP endpoint for remote MCP access
Supports both authenticated (path-based) and unauthenticated modes
Optimized for scale-to-zero scenarios with lazy initialization
"""

import os
import sys
import logging
import hashlib
import asyncio
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv('.env.local')
load_dotenv('.env')

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if os.getenv('DEBUG', 'false').lower() == 'true' else logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Get API key from environment (optional)
api_key = os.getenv("MCP_API_KEY")
md5_salt = os.getenv("MD5_SALT", "")

# Track initialization state for lazy loading
_services_initialized = False

def lazy_initialize_services():
    """
    Lazy initialization of services - called on first request instead of at startup.
    This dramatically improves cold start time for scale-to-zero scenarios.
    """
    global _services_initialized

    if _services_initialized:
        return

    logger.info("Lazy initializing services on first request...")

    from .server import initialize_services
    initialize_services()

    _services_initialized = True
    logger.info("Services initialized successfully")


if api_key:
    # Use path-based authentication if API key is set
    logger.info("MCP_API_KEY is set - using path-based authentication")

    from fastapi import FastAPI, Request, HTTPException
    from fastapi.responses import JSONResponse
    from starlette.middleware.base import BaseHTTPMiddleware
    from .server import mcp, get_ical_service

    # Validate API key format (prevent path traversal attacks)
    if not api_key.replace("-", "").replace("_", "").isalnum():
        logger.error("API key contains invalid characters. Use only alphanumeric, dash, and underscore.")
        sys.exit(1)

    if len(api_key) < 16:
        logger.warning("API key is too short. Consider using a longer key for better security.")

    # Calculate MD5 hash of API key with optional salt for additional security layer
    if md5_salt:
        logger.info(f"Using MD5 salt from MD5_SALT environment variable")
        hash_input = f"{md5_salt}{api_key}"
    else:
        logger.warning("No MD5_SALT configured - using unsalted hash")
        hash_input = api_key

    api_key_hash = hashlib.md5(hash_input.encode()).hexdigest()
    logger.info(f"API key hash calculated: {api_key_hash[:8]}... (showing first 8 chars)")

    # Get configuration
    port = int(os.getenv("PORT", "8080"))
    host = os.getenv("HOST", "0.0.0.0")

    # Check configuration
    if not os.getenv('ICAL_FEED_CONFIGS'):
        logger.warning("No iCalendar feeds configured - will initialize on first request")

    # DO NOT initialize services here - lazy init on first request
    # This allows the container to start immediately

    # Security middleware to add headers
    class SecurityMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            response = await call_next(request)

            # Add security headers
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["X-XSS-Protection"] = "1; mode=block"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"

            # Remove server identification headers if they exist
            if "server" in response.headers:
                del response.headers["server"]
            if "x-powered-by" in response.headers:
                del response.headers["x-powered-by"]

            return response

    # Middleware to lazy-initialize services on first real request
    class LazyInitMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            # Skip lazy init for health check (keeps it fast)
            if request.url.path != "/app/health":
                lazy_initialize_services()

            return await call_next(request)

    # Get the MCP HTTP app without a path since we'll mount it at /mcp
    mcp_app = mcp.http_app()

    # Create FastAPI app with security settings and MCP lifespan
    app = FastAPI(
        title="MattasMCP Remote Server",
        docs_url=None,  # Disable Swagger UI
        redoc_url=None,  # Disable ReDoc
        openapi_url=None,  # Disable OpenAPI schema
        lifespan=mcp_app.lifespan  # REQUIRED: Connect MCP app's lifespan
    )

    # Add middlewares (order matters - security first, then lazy init)
    app.add_middleware(SecurityMiddleware)
    app.add_middleware(LazyInitMiddleware)

    # Ultra-lightweight health check endpoint - no service initialization
    # This endpoint MUST be fast to pass health checks during cold starts
    @app.get("/app/health")
    async def health_check():
        """
        Lightweight health check endpoint for container orchestrators.
        Does NOT trigger service initialization to keep cold starts fast.
        """
        return {
            "status": "healthy",
            "initialized": _services_initialized,
            "version": "2.0.0"
        }

    # Mount the MCP app at /app/{api_key}/{api_key_hash}
    # The MCP app has internal routes like /mcp, /sse, etc.
    app.mount(f"/app/{api_key}/{api_key_hash}", mcp_app)

    # Add a custom 404 handler with anti-brute-force delay
    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc: HTTPException):
        # Add 30-second delay for failed authentication attempts to prevent brute forcing
        # Only delay for /app/ paths that look like authentication attempts
        if request.url.path.startswith("/app/") and request.url.path != "/app/health":
            logger.warning(f"Invalid authentication path attempted: {request.url.path} from {request.client.host if request.client else 'unknown'}")
            await asyncio.sleep(30)

        return JSONResponse(
            status_code=404,
            content={"detail": "Not Found"}
        )

    if __name__ == "__main__":
        # When run directly (not via uvicorn CLI)
        import uvicorn

        logger.info("Starting MattasMCP remote server with dual-factor path authentication")
        logger.info(f"MCP endpoint: http://{host}:{port}/app/{api_key}/{api_key_hash}/mcp")
        logger.info(f"Health check (public): http://{host}:{port}/app/health")
        logger.info(f"API Key Hash: {api_key_hash}")
        logger.warning("Keep your API key secret and use HTTPS in production!")
        logger.info("Services will initialize lazily on first MCP request")

        uvicorn.run(
            app,
            host=host,
            port=port,
            loop="uvloop",  # Use uvloop for better performance
            http="httptools",  # Use httptools for faster HTTP parsing
            log_level="warning",
            access_log=False,  # Disable access logs to prevent API key leakage
            server_header=False,
            date_header=False
        )

else:
    # Use simple unauthenticated mode if no API key is set
    logger.warning("MCP_API_KEY not set - running in UNAUTHENTICATED mode")
    logger.warning("This is not recommended for production use!")

    from .server import mcp, get_ical_service

    # DO NOT initialize services here - lazy init on first request

    # For unauthenticated mode, we still use the mcp.run() method
    # but we should avoid eager initialization
    if __name__ == "__main__":
        # Get configuration
        port = int(os.getenv("PORT", "8080"))
        host = os.getenv("HOST", "0.0.0.0")

        # Check configuration
        if not os.getenv('ICAL_FEED_CONFIGS'):
            logger.warning("No iCalendar feeds configured - will initialize on first request")

        # For unauthenticated mode, let FastMCP handle everything
        # FastMCP will initialize services when needed
        logger.info("Starting MattasMCP remote server (UNAUTHENTICATED)")
        logger.info(f"MCP endpoint: http://{host}:{port}/mcp")
        logger.info("Note: Set MCP_API_KEY environment variable to enable authentication")
        logger.info("Services will initialize lazily on first MCP request")

        # Start the server with HTTP transport
        mcp.run(transport="http", host=host, port=port)
