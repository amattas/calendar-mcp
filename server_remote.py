#!/usr/bin/env python3
"""
MattasMCP Remote Server - exposes HTTP endpoint for remote MCP access
Supports both authenticated (path-based) and unauthenticated modes
Designed for Azure Container Apps which handles SSL termination
"""

import os
import sys
import logging
import hashlib
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

if api_key:
    # Use path-based authentication if API key is set
    logger.info("MCP_API_KEY is set - using path-based authentication")
    
    from fastapi import FastAPI, Request, HTTPException, Response
    from fastapi.responses import JSONResponse, PlainTextResponse
    from fastapi.middleware.cors import CORSMiddleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from contextlib import asynccontextmanager
    import uvicorn
    from server import mcp, get_ical_service, initialize_services

    # Get configuration
    port = int(os.getenv("PORT", "80"))
    host = os.getenv("HOST", "0.0.0.0")

    # Check configuration
    if not os.getenv('ICAL_FEED_CONFIGS'):
        logger.warning("No iCalendar feeds configured")
    
    # Validate API key format (prevent path traversal attacks)
    if not api_key.replace("-", "").replace("_", "").isalnum():
        logger.error("API key contains invalid characters. Use only alphanumeric, dash, and underscore.")
        sys.exit(1)
    
    if len(api_key) < 16:
        logger.warning("API key is too short. Consider using a longer key for better security.")

    # Calculate MD5 hash of API key for additional security layer
    api_key_hash = hashlib.md5(api_key.encode()).hexdigest()
    logger.info(f"API key hash calculated: {api_key_hash[:8]}... (showing first 8 chars)")

    # Initialize services BEFORE creating the MCP HTTP app
    logger.info("Initializing services for authenticated mode...")
    initialize_services()
    logger.info("Services initialized successfully")
    
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
    
    # Add security middleware
    app.add_middleware(SecurityMiddleware)
    
    # Create a simple public health check endpoint
    @app.get("/health")
    async def health_check():
        """Public health check endpoint for monitoring"""
        services = {
            "ical": get_ical_service() is not None
        }

        return {
            "status": "healthy",
            "services": services,
            "version": "2.0.0"
        }

    # Mount the MCP app at /{api_key}/{api_key_hash} - it will handle /mcp internally
    app.mount(f"/{api_key}/{api_key_hash}", mcp_app)
    
    # Add a custom 404 handler instead of catch-all route
    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc: HTTPException):
        logger.warning("Access attempt to undefined route")
        return JSONResponse(
            status_code=404,
            content={"detail": "Not Found"}
        )
    
    if __name__ == "__main__":
        # Run HTTP server with authentication
        logger.info("Starting MattasMCP remote server with dual-factor path authentication")
        logger.info(f"MCP endpoint: http://{host}:{port}/{api_key}/{api_key_hash}/mcp")
        logger.info(f"Health check (public): http://{host}:{port}/health")
        logger.info(f"API Key Hash: {api_key_hash}")
        logger.warning("Keep your API key secret and use HTTPS in production!")
        
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level="warning",  # Reduce log verbosity
            access_log=False,  # Disable access logs to prevent API key leakage
            server_header=False,  # Don't send server header
            date_header=False  # Don't send date header
        )

else:
    # Use simple unauthenticated mode if no API key is set
    logger.warning("MCP_API_KEY not set - running in UNAUTHENTICATED mode")
    logger.warning("This is not recommended for production use!")

    from server import mcp, get_ical_service, initialize_services

    if __name__ == "__main__":
        # Get configuration
        port = int(os.getenv("PORT", "80"))
        host = os.getenv("HOST", "0.0.0.0")

        # Check configuration
        if not os.getenv('ICAL_FEED_CONFIGS'):
            logger.warning("No iCalendar feeds configured")
        
        # Initialize services before starting the server
        logger.info("Initializing services...")
        initialize_services()
        logger.info("Services initialized successfully")
        
        # Run HTTP server without authentication
        logger.info("Starting MattasMCP remote server (UNAUTHENTICATED)")
        logger.info(f"MCP endpoint: http://{host}:{port}/mcp")
        logger.info("Note: Set MCP_API_KEY environment variable to enable authentication")
        
        # Start the server with HTTP transport
        mcp.run(transport="http", host=host, port=port)