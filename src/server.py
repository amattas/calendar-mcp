#!/usr/bin/env python3
"""
Calendar MCP Server
A specialized MCP server for iCalendar feed management and event queries
"""

import os
import sys
import logging
from typing import Optional, Dict, Any
from dotenv import dotenv_values
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from fastmcp import FastMCP

# Import our service modules
from .services.ical import MultiCalendarService
from .services.cache import RedisCache

# Load environment variables with correct precedence
config: Dict[str, str] = {}

# Load from project directory if available
for filename in ('.env', '.env.local'):
    path = Path(filename)
    if path.exists():
        config.update(dotenv_values(path))

# Also check the script's directory (supports running from elsewhere)
script_dir = Path(__file__).parent
for filename in ('.env', '.env.local'):
    path = script_dir / filename
    if path.exists():
        config.update(dotenv_values(path))

# Apply loaded values without overriding existing environment vars
for key, value in config.items():
    os.environ.setdefault(key, value)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if os.getenv('DEBUG', 'false').lower() == 'true' else logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize MCP server
mcp = FastMCP(name="CalendarMCP")

# Service instances (will be initialized on first use)
_ical_service: Optional[MultiCalendarService] = None
_ical_service_config: Optional[str] = None  # Track the config used to create the service
_cache_service: Optional[RedisCache] = None


def get_ical_service() -> Optional[MultiCalendarService]:
    """Get or initialize the iCalendar service"""
    global _ical_service, _ical_service_config

    # Get current configuration
    current_config = os.getenv('ICAL_FEED_CONFIGS', '')

    # Check if configuration has changed or service doesn't exist
    if _ical_service is None or _ical_service_config != current_config:
        # Clean up old service if it exists
        if _ical_service is not None:
            logger.info("Configuration changed, reinitializing calendar service")
            _ical_service.stop()  # Stop the refresh timer
            _ical_service = None

        # Parse calendar configurations
        feed_configs = []
        configs_str = os.getenv('ICAL_FEED_CONFIGS')
        if not configs_str:
            logger.warning("No iCalendar feeds configured")
            return None

        import json

        # Check if this is a Name="[JSON]" format (malformed but common)
        if '=' in configs_str and ('"[' in configs_str or "'[" in configs_str):
            # Extract the JSON part after the equals sign
            try:
                json_part = configs_str.split('=', 1)[1].strip()
                # Remove surrounding quotes if present
                if (json_part.startswith('"') and json_part.endswith('"')) or \
                   (json_part.startswith("'") and json_part.endswith("'")):
                    json_part = json_part[1:-1]
                # Try to parse the JSON part
                feed_configs = json.loads(json_part)
                if isinstance(feed_configs, dict):
                    feed_configs = [feed_configs]
                logger.info(f"Parsed {len(feed_configs)} feeds from Name=JSON format")
            except (json.JSONDecodeError, IndexError) as e:
                logger.error(f"Failed to parse Name=JSON format: {e}")
                # Continue to fallback methods below

        # If not parsed yet, try standard JSON format
        if not feed_configs:
            # First, try to clean up Azure's escaped JSON
            cleaned_str = configs_str

            # Handle Azure's common escaping patterns
            if configs_str.startswith('"') and configs_str.endswith('"'):
                # Remove outer quotes that Azure might add
                cleaned_str = configs_str[1:-1]

            # Replace escaped quotes
            if '\\' in cleaned_str:
                cleaned_str = cleaned_str.replace('\\"', '"').replace('\\\\', '\\')

            try:
                feed_configs = json.loads(cleaned_str)
                if isinstance(feed_configs, dict):
                    # Allow single dict
                    feed_configs = [feed_configs]
                logger.info(f"Parsed {len(feed_configs)} feeds from JSON config")
            except json.JSONDecodeError as e:
                # Only fall back to delimiter format if it's clearly not JSON
                # Check if it looks like JSON array/object
                if configs_str.strip().startswith('[') or configs_str.strip().startswith('{'):
                    logger.error(f"Failed to parse JSON config: {e}")
                    logger.error(f"Config string: {configs_str[:200]}...")
                    return None

                # Fall back to delimiter separated format only for simple formats
                delimiter = ';' if ';' in configs_str else ','
                # Only split if we don't see JSON structures
                if '"url"' not in configs_str and '"name"' not in configs_str:
                    for part in configs_str.split(delimiter):
                        part = part.strip()
                        if not part:
                            continue
                        if '=' in part:
                            name, url = part.split('=', 1)
                            feed_configs.append({'name': name.strip(), 'url': url.strip()})
                        else:
                            feed_configs.append({'name': None, 'url': part.strip()})
                    if feed_configs:
                        logger.info(f"Parsed {len(feed_configs)} feeds from delimited config")
                else:
                    logger.error(f"Config appears to be malformed JSON, not attempting delimiter split")
                    return None

        if not feed_configs:
            logger.warning("No iCalendar feeds configured")
            return None

        refresh_interval = int(os.getenv('REFRESH_INTERVAL', '60'))
        try:
            # Get cache service if available
            cache = get_cache_service()
            _ical_service = MultiCalendarService(
                feed_configs=feed_configs,
                refresh_interval_minutes=refresh_interval,
                mcp=mcp,  # Pass MCP instance to service
                cache=cache  # Pass cache instance to service
            )
            _ical_service_config = current_config  # Save the config that was used
            logger.info(f"Initialized iCalendar service with {len(feed_configs)} feeds" + (" with caching" if cache else ""))
        except Exception as e:
            logger.error(f"Failed to initialize iCalendar service: {e}")
            return None

    return _ical_service


def get_cache_service() -> Optional[RedisCache]:
    """Get or initialize the Redis cache service"""
    global _cache_service

    if _cache_service is None:
        try:
            _cache_service = RedisCache.from_env()
            if _cache_service and _cache_service.is_connected():
                logger.info("Redis cache service initialized successfully")
            else:
                _cache_service = None
                logger.warning("Redis cache service not available")
        except Exception as e:
            logger.error(f"Failed to initialize Redis cache: {e}")
            _cache_service = None

    return _cache_service


# Register additional server-level tools
@mcp.tool(
    name="get_current_datetime",
    description="""Get the current date and time in the configured timezone.

## Returns
• Current date (YYYY-MM-DD format)
• Current time (HH:MM:SS format)
• Current datetime (ISO 8601 format)
• Configured timezone name
• UTC offset

## Use Cases
• Reference current date/time for calendar queries
• Understand timezone context for events
• Schedule relative to current time

## Related Tools
• Use `get_events_today` for today's calendar events
• Use `get_upcoming_events` for future events

⚠️ **Note**: The timezone is configured via the TIMEZONE environment variable (default: UTC)""",
    title="Current Date & Time",
    annotations={"title": "Current Date & Time"}
)
def get_current_datetime() -> Dict[str, Any]:
    """Get the current date and time in the configured timezone"""
    # Get timezone from environment variable, default to UTC
    timezone_str = os.getenv('TIMEZONE', 'UTC')

    try:
        tz = ZoneInfo(timezone_str)
    except Exception as e:
        logger.warning(f"Invalid timezone '{timezone_str}': {e}. Falling back to UTC.")
        tz = ZoneInfo('UTC')
        timezone_str = 'UTC'

    # Get current datetime in the configured timezone
    now = datetime.now(tz)

    return {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "datetime": now.isoformat(),
        "timezone": timezone_str,
        "utc_offset": now.strftime("%z"),
        "timezone_abbr": now.strftime("%Z"),
        "day_of_week": now.strftime("%A"),
        "timestamp": int(now.timestamp())
    }


@mcp.tool(
    name="get_server_status",
    description="""Get the current status of the calendar service.

## Returns
• Service status for iCalendar integration
• Active feeds and refresh interval
• Overall server version

## Use Cases
• Health check
• Service monitoring
• Troubleshooting connections

## Related Tools
• Use `get_server_config` for configuration details""",
    title="Server Status",
    annotations={"title": "Server Status"}
)
def get_server_status() -> Dict[str, Any]:
    """Get the status of the calendar service"""
    status = {
        "server": "CalendarMCP",
        "version": "1.0.0",
        "services": {}
    }

    # Check iCalendar service
    ical_service = get_ical_service()
    if ical_service:
        info = ical_service.get_calendar_info()
        status["services"]["icalendar"] = {
            "status": "active",
            "feeds": info.get("total_feeds", 0),
            "refresh_interval": info.get("refresh_interval_minutes", 60)
        }
    else:
        status["services"]["icalendar"] = {"status": "not_configured"}

    return status


@mcp.tool(
    name="get_server_config",
    description="""Get the current server configuration (non-sensitive values only).

## Returns
• Debug mode status
• Refresh interval settings
• Service configuration status

## Use Cases
• Check configuration
• Verify settings
• Debug issues

## Related Tools
• Use `get_server_status` for service health

⚠️ **Note**: Sensitive values like API keys are not exposed""",
    title="Server Configuration",
    annotations={"title": "Server Configuration"}
)
def get_server_config() -> Dict[str, Any]:
    """Get the current server configuration (non-sensitive)"""
    return {
        "debug_mode": os.getenv('DEBUG', 'false').lower() == 'true',
        "refresh_interval": int(os.getenv('REFRESH_INTERVAL', '60')),
        "ical_feeds_configured": bool(os.getenv('ICAL_FEED_CONFIGS'))
    }


# ==================== Cache Management Tools ====================

@mcp.tool(
    name="get_cache_stats",
    description="""Get Redis cache statistics and performance metrics.

## Returns
• Hit/miss rates
• Average response times
• Error counts
• Total requests
• Uptime

## Use Cases
• Monitor cache performance
• Debug caching issues
• Optimize cache configuration

## Related Tools
• Use `clear_cache` to clear cache entries
• Use `get_cache_info` for Redis server info""",
    title="Cache Statistics",
    annotations={"title": "Cache Statistics"}
)
def get_cache_stats() -> Dict[str, Any]:
    """Get cache statistics"""
    cache = get_cache_service()
    if not cache:
        return {"error": "Cache service not available"}

    stats = cache.get_stats()
    return stats.to_dict()


@mcp.tool(
    name="clear_cache",
    description="""Clear cache entries by pattern or all cache data.

## Parameters
• pattern: Pattern to match keys (e.g., "ical:*"). If not provided, clears ALL cache.

## Use Cases
• Clear stale data
• Force refresh of cached data
• Debug caching issues

## Related Tools
• Use `get_cache_stats` to view cache metrics
• Use `get_cache_info` for Redis server info

⚠️ **Warning**: Clearing all cache may impact performance temporarily""",
    title="Clear Cache",
    annotations={"title": "Clear Cache"}
)
def clear_cache(pattern: Optional[str] = None) -> Dict[str, Any]:
    """Clear cache entries"""
    cache = get_cache_service()
    if not cache:
        return {"error": "Cache service not available"}

    if pattern:
        # Clear by pattern
        deleted = cache.delete_pattern(pattern)
        return {
            "status": "success",
            "pattern": pattern,
            "keys_deleted": deleted
        }
    else:
        # Clear all cache
        if cache.flush_all():
            return {
                "status": "success",
                "message": "All cache cleared"
            }
        else:
            return {
                "status": "error",
                "message": "Failed to clear cache"
            }


@mcp.tool(
    name="get_cache_info",
    description="""Get Redis server information and cache configuration.

## Returns
• Redis version
• Memory usage
• Connected clients
• Keyspace info
• Configuration details

## Use Cases
• Monitor cache health
• Check Redis server status
• View cache configuration

## Related Tools
• Use `get_cache_stats` for performance metrics
• Use `clear_cache` to clear cache entries""",
    title="Cache Information",
    annotations={"title": "Cache Information"}
)
def get_cache_info() -> Dict[str, Any]:
    """Get Redis server information"""
    cache = get_cache_service()
    if not cache:
        return {"error": "Cache service not available"}

    info = cache.info()

    # Extract key information
    return {
        "connected": cache.is_connected(),
        "host": cache.host,
        "port": cache.port,
        "ssl_enabled": cache.use_ssl,
        "server": {
            "redis_version": info.get("redis_version", "unknown"),
            "uptime_seconds": info.get("uptime_in_seconds", 0),
            "connected_clients": info.get("connected_clients", 0),
            "used_memory_human": info.get("used_memory_human", "unknown"),
            "used_memory_peak_human": info.get("used_memory_peak_human", "unknown")
        },
        "keyspace": {
            db: stats for db, stats in info.items()
            if db.startswith("db")
        }
    }


@mcp.tool(
    name="reset_cache_stats",
    description="""Reset cache performance statistics.

## Use Cases
• Start fresh monitoring period
• Clear old statistics
• Begin new performance measurement

## Related Tools
• Use `get_cache_stats` to view current statistics""",
    title="Reset Cache Statistics",
    annotations={"title": "Reset Cache Statistics"}
)
def reset_cache_stats() -> Dict[str, Any]:
    """Reset cache statistics"""
    cache = get_cache_service()
    if not cache:
        return {"error": "Cache service not available"}

    cache.reset_stats()
    return {
        "status": "success",
        "message": "Cache statistics reset"
    }


# Initialize services on startup
def initialize_services():
    """Initialize all configured services"""
    logger.info("Initializing calendar service...")

    # Initialize iCalendar service
    ical = get_ical_service()
    if ical:
        logger.info("✓ iCalendar service initialized")


if __name__ == "__main__":
    # Run the MCP server
    logger.info("Starting CalendarMCP server...")

    # Initialize services
    initialize_services()

    # Check configuration
    if not os.getenv('ICAL_FEED_CONFIGS'):
        logger.warning("No iCalendar feeds configured. Set ICAL_FEED_CONFIGS in .env.local or .env")

    # Run the server using stdio transport
    mcp.run()
