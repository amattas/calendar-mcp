"""Service for fetching and caching multiple named iCalendar feeds"""

import logging
from datetime import datetime, timedelta, timezone, date
from typing import Dict, List, Optional, Any
from threading import Lock, Timer
import requests
from icalendar import Calendar, Event
from dateutil import parser as date_parser
from dateutil.tz import UTC
import hashlib
import recurring_ical_events
from typing import TYPE_CHECKING
from services.cache import cache_aside, CacheConfig, CacheTTL

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from services.cache import RedisCache

logger = logging.getLogger(__name__)


class CalendarFeed:
    """Represents a named calendar feed"""
    
    def __init__(self, url: str, name: str = None):
        self.url = url
        self.name = name or self._generate_name_from_url(url)
        self.id = hashlib.md5(url.encode()).hexdigest()[:8]
        self.calendar: Optional[Calendar] = None
        self.last_fetch: Optional[datetime] = None
        self.error: Optional[str] = None
    
    def _generate_name_from_url(self, url: str) -> str:
        """Generate a default name from URL"""
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc.replace('www.', '').split('.')[0]
        path = parsed.path.strip('/').split('/')[-1] if parsed.path else ''
        if path and not path.endswith('.ics'):
            return f"{domain}-{path}"
        return domain or "calendar"


class MultiCalendarService:
    """Service for fetching, caching, and querying multiple named iCalendar feeds"""
    
    def __init__(self, feed_configs: List[Dict[str, str]], refresh_interval_minutes: int = 60, mcp: Optional['FastMCP'] = None, cache: Optional['RedisCache'] = None):
        """
        Initialize the multi-calendar service
        
        Args:
            feed_configs: List of feed configurations with 'url' and optional 'name'
            refresh_interval_minutes: How often to refresh the cache (in minutes)
        """
        self.feeds: Dict[str, CalendarFeed] = {}
        self.refresh_interval = refresh_interval_minutes * 60  # Convert to seconds
        self._lock = Lock()
        self._refresh_timer: Optional[Timer] = None
        self.mcp = mcp
        self.cache = cache
        
        # Initialize feeds
        for config in feed_configs:
            url = config.get('url')
            name = config.get('name')

            if url:
                feed = CalendarFeed(url, name)
                self.feeds[feed.id] = feed
        
        # Perform initial fetch for all feeds
        self.refresh_all_calendars()
        
        # Schedule periodic refresh
        self._schedule_refresh()
        
        # Register MCP tools if MCP server is provided
        if self.mcp:
            self._register_mcp_tools()
    
    # ========== VALIDATION HELPERS ==========
    
    def _validate_url(self, url: str) -> None:
        """Validate calendar URL format"""
        if not url:
            raise ValueError(
                "Calendar URL is required.\n"
                "Please provide a valid .ics calendar URL.\n"
                "To find calendar URLs:\n"
                "  • Google Calendar: Settings → Calendar → Secret address in iCal format\n"
                "  • Outlook: Settings → Shared calendars → Publish calendar → ICS link\n"
                "  • Apple Calendar: Right-click calendar → Share Settings → Public Calendar"
            )
        
        if not (url.startswith('http://') or url.startswith('https://') or url.startswith('webcal://')):
            raise ValueError(
                f"Invalid URL format: '{url}'.\n"
                "Calendar URLs must start with http://, https://, or webcal://.\n"
                "Examples:\n"
                "  • https://calendar.google.com/calendar/ical/example.ics\n"
                "  • webcal://calendar.example.com/feed.ics\n"
                "  • https://outlook.live.com/owa/calendar/example.ics"
            )
    
    def _validate_date_format(self, date_str: Optional[str], param_name: str) -> None:
        """Validate date format"""
        if date_str is not None:
            try:
                datetime.strptime(date_str, '%Y-%m-%d')
            except ValueError:
                raise ValueError(
                    f"Invalid {param_name} format: '{date_str}'.\n"
                    "Date must be in YYYY-MM-DD format.\n"
                    "Examples:\n"
                    "  • '2024-12-31' for December 31, 2024\n"
                    "  • '2024-01-15' for January 15, 2024\n"
                    "Or use natural language:\n"
                    "  • 'today' for current date\n"
                    "  • 'tomorrow' for next day\n"
                    "  • '7 days' for a week from now"
                )
    
    def _validate_feed_exists(self, feed_identifier: str) -> 'CalendarFeed':
        """Validate that a feed exists and return it"""
        feed = self._find_feed(feed_identifier)
        if not feed:
            available_feeds = [f"{f.name} (ID: {f.id})" for f in self.feeds.values()]
            raise ValueError(
                f"Calendar feed '{feed_identifier}' not found.\n"
                f"Available feeds: {', '.join(available_feeds) if available_feeds else 'None'}\n"
                "To manage feeds:\n"
                "  • Use `list_calendar_feeds` to see all feeds\n"
                "  • Use `add_calendar_feed` to add a new feed\n"
                "  • Feed can be identified by name, ID, or URL"
            )
        return feed
    
    def _schedule_refresh(self):
        """Schedule the next automatic refresh"""
        if self._refresh_timer:
            self._refresh_timer.cancel()
        
        self._refresh_timer = Timer(self.refresh_interval, self._auto_refresh)
        self._refresh_timer.daemon = True
        self._refresh_timer.start()
    
    def _auto_refresh(self):
        """Automatically refresh all calendars and reschedule"""
        try:
            self.refresh_all_calendars()
        except Exception as e:
            logger.error(f"Auto-refresh failed: {e}")
        finally:
            self._schedule_refresh()
    
    def refresh_all_calendars(self) -> Dict[str, Any]:
        """Fetch and cache all calendars from the feed URLs"""
        results = []
        
        for feed_id, feed in self.feeds.items():
            result = self._refresh_single_calendar(feed)
            results.append(result)
        
        return {
            'status': 'success',
            'feeds_refreshed': len(self.feeds),
            'results': results
        }
    
    def refresh_calendar(self, feed_identifier: Optional[str] = None) -> Dict[str, Any]:
        """Refresh a specific calendar or all calendars"""
        if feed_identifier:
            feed = self._find_feed(feed_identifier)
            if feed:
                return self._refresh_single_calendar(feed)
            else:
                return {
                    'status': 'error',
                    'error': f'Feed not found: {feed_identifier}'
                }
        else:
            return self.refresh_all_calendars()
    
    def _find_feed(self, identifier: str) -> Optional[CalendarFeed]:
        """Find a feed by URL, name, or ID"""
        if identifier in self.feeds:
            return self.feeds[identifier]
        
        for feed in self.feeds.values():
            if feed.url == identifier or feed.name == identifier:
                return feed
        
        return None
    
    def _refresh_single_calendar(self, feed: CalendarFeed) -> Dict[str, Any]:
        """Refresh a single calendar"""
        with self._lock:
            try:
                logger.info(f"Fetching calendar '{feed.name}' from: {feed.url}")
                response = requests.get(feed.url, timeout=30)
                response.raise_for_status()
                
                calendar = Calendar.from_ical(response.content)
                feed.calendar = calendar
                feed.last_fetch = datetime.now(UTC)
                feed.error = None
                
                event_count = sum(1 for comp in calendar.walk() if comp.name == "VEVENT")
                
                return {
                    "status": "success",
                    "feed_url": feed.url,
                    "feed_name": feed.name,
                    "feed_id": feed.id,
                    "last_fetch": feed.last_fetch.isoformat(),
                    "event_count": event_count,
                    "calendar_name": str(calendar.get('X-WR-CALNAME', feed.name))
                }
            except requests.exceptions.Timeout:
                error_msg = (
                    f"Calendar feed '{feed.name}' timed out after 30 seconds.\n"
                    "Possible issues:\n"
                    "  • The calendar server is slow or unresponsive\n"
                    "  • Network connectivity issues\n"
                    "  • The URL might be incorrect\n"
                    "To fix:\n"
                    "  • Try refreshing again with `refresh_calendars`\n"
                    "  • Verify the calendar URL is accessible\n"
                    "  • Check if the calendar provider is online"
                )
                logger.error(f"Timeout fetching feed '{feed.name}'")
                feed.error = "Connection timeout"
                return {
                    "status": "error",
                    "feed_url": feed.url,
                    "feed_name": feed.name,
                    "feed_id": feed.id,
                    "error": error_msg,
                    "last_fetch": feed.last_fetch.isoformat() if feed.last_fetch else None
                }
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 401:
                    error_msg = (
                        f"Authentication failed for calendar '{feed.name}'.\n"
                        "The calendar requires authentication.\n"
                        "To fix:\n"
                        "  • Check if the calendar URL includes authentication tokens\n"
                        "  • Regenerate the calendar's secret URL\n"
                        "  • Make sure the calendar is set to public or has proper access"
                    )
                elif e.response.status_code == 404:
                    error_msg = (
                        f"Calendar feed '{feed.name}' not found (404).\n"
                        "The calendar URL is invalid or has been removed.\n"
                        "To fix:\n"
                        "  • Verify the calendar URL is correct\n"
                        "  • Get a new sharing URL from your calendar provider\n"
                        "  • Use `remove_calendar_feed` to remove this feed\n"
                        "  • Use `add_calendar_feed` with the correct URL"
                    )
                else:
                    error_msg = f"HTTP error {e.response.status_code} for calendar '{feed.name}': {str(e)}"
                
                logger.error(f"HTTP error fetching feed '{feed.name}': {e}")
                feed.error = str(e)
                return {
                    "status": "error",
                    "feed_url": feed.url,
                    "feed_name": feed.name,
                    "feed_id": feed.id,
                    "error": error_msg,
                    "last_fetch": feed.last_fetch.isoformat() if feed.last_fetch else None
                }
            except Exception as e:
                error_msg = (
                    f"Failed to fetch calendar '{feed.name}': {str(e)}\n"
                    "To diagnose:\n"
                    "  • Use `list_calendar_feeds` to check feed status\n"
                    "  • Try removing and re-adding the feed\n"
                    "  • Verify the calendar URL format is correct"
                )
                logger.error(f"Failed to fetch calendar '{feed.name}' from {feed.url}: {e}")
                feed.error = str(e)
                return {
                    "status": "error",
                    "feed_url": feed.url,
                    "feed_name": feed.name,
                    "feed_id": feed.id,
                    "error": error_msg,
                    "last_fetch": feed.last_fetch.isoformat() if feed.last_fetch else None
                }
    
    def _event_to_dict(self, event: Event, feed: CalendarFeed) -> Dict[str, Any]:
        """Convert an iCalendar event to a dictionary with feed information"""
        def safe_str(value):
            if value is None:
                return None
            if hasattr(value, 'dt'):
                dt = value.dt
                if isinstance(dt, datetime):
                    # Normalize to UTC before serializing
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    else:
                        dt = dt.astimezone(UTC)
                    return dt.isoformat()
                else:
                    return dt.isoformat() if hasattr(dt, 'isoformat') else str(dt)
            return str(value)
        
        # Handle DTEND - if missing, try to use DURATION or default to 1 hour
        dtstart = event.get('DTSTART')
        dtend = event.get('DTEND')
        duration = event.get('DURATION')
        
        # If DTEND is missing but we have DURATION, calculate end time
        if not dtend and duration and dtstart:
            try:
                if hasattr(dtstart, 'dt'):
                    start_dt = dtstart.dt
                    if hasattr(duration, 'dt'):
                        # Normalize start time to UTC first
                        if isinstance(start_dt, datetime):
                            if start_dt.tzinfo is None:
                                start_dt = start_dt.replace(tzinfo=UTC)
                            else:
                                start_dt = start_dt.astimezone(UTC)
                        
                        # duration.dt is a timedelta
                        end_dt = start_dt + duration.dt
                        dtend = end_dt
            except:
                pass  # Fall back to None
        
        # If still no end time and we have a start time, default to 1 hour duration
        if not dtend and dtstart:
            try:
                if hasattr(dtstart, 'dt'):
                    start_dt = dtstart.dt
                    if isinstance(start_dt, datetime):
                        # Normalize start time to UTC first
                        if start_dt.tzinfo is None:
                            start_dt = start_dt.replace(tzinfo=UTC)
                        else:
                            start_dt = start_dt.astimezone(UTC)
                        
                        # For datetime events, add 1 hour
                        from datetime import timedelta
                        dtend = start_dt + timedelta(hours=1)
                    else:
                        # For date-only (all-day) events, end is same as start
                        dtend = start_dt
            except:
                pass
        
        event_dict = {
            "uid": safe_str(event.get('UID')),
            "summary": safe_str(event.get('SUMMARY')),
            "description": safe_str(event.get('DESCRIPTION')),
            "location": safe_str(event.get('LOCATION')),
            "start": safe_str(dtstart),
            "end": safe_str(dtend),
            "all_day": False,
            "status": safe_str(event.get('STATUS')),
            "organizer": safe_str(event.get('ORGANIZER')),
            "attendees": [],
            "categories": [],
            "recurrence": safe_str(event.get('RRULE')),
            "source_feed": feed.url,
            "source_feed_name": feed.name,
            "source_feed_id": feed.id
        }
        
        # Check if it's an all-day event
        if dtstart and hasattr(dtstart, 'dt'):
            if not isinstance(dtstart.dt, datetime):
                event_dict["all_day"] = True
        
        # Handle attendees
        attendees = event.get('ATTENDEE', [])
        if not isinstance(attendees, list):
            attendees = [attendees]
        event_dict["attendees"] = [safe_str(a) for a in attendees if a]
        
        # Handle categories
        categories = event.get('CATEGORIES')
        if categories:
            if hasattr(categories, 'cats'):
                event_dict["categories"] = categories.cats
            elif isinstance(categories, list):
                event_dict["categories"] = categories
            else:
                event_dict["categories"] = [str(categories)]
        
        return event_dict
    
    def _normalize_datetime(self, dt) -> Optional[datetime]:
        """Normalize various date/datetime formats to UTC datetime"""
        if dt is None:
            return None
        
        if isinstance(dt, datetime):
            if dt.tzinfo is None:
                # Naive datetime - assume UTC for consistency
                return dt.replace(tzinfo=UTC)
            else:
                # Timezone-aware datetime - convert to UTC
                return dt.astimezone(UTC)
        
        if isinstance(dt, str):
            try:
                parsed = date_parser.parse(dt)
                if parsed.tzinfo is None:
                    # Naive datetime from string - assume UTC
                    return parsed.replace(tzinfo=UTC)
                else:
                    # Timezone-aware datetime from string - convert to UTC
                    return parsed.astimezone(UTC)
            except:
                return None
        
        # Handle date objects (convert to UTC datetime at midnight)
        if hasattr(dt, 'year') and hasattr(dt, 'month') and hasattr(dt, 'day'):
            return datetime(dt.year, dt.month, dt.day, tzinfo=UTC)
        
        return None
    
    @cache_aside(CacheConfig(ttl=CacheTTL.CALENDAR_EVENTS, key_prefix="ical:events"))
    def get_events(self, start_date: Optional[str] = None, end_date: Optional[str] = None, 
                   feed_identifiers: Optional[List[str]] = None, limit: Optional[int] = None,
                   offset: int = 0) -> List[Dict[str, Any]]:
        """Get events within a date range from specified feeds or all feeds, expanding recurring events
        
        Args:
            start_date: Start date (ISO format or YYYY-MM-DD)
            end_date: End date (ISO format or YYYY-MM-DD)
            feed_identifiers: Optional list of feed IDs or names to filter
            limit: Maximum number of results to return (for pagination)
            offset: Number of results to skip (for pagination)
        
        Returns:
            List of event dictionaries
        """
        events = []
        
        # Validate feed identifiers if provided
        if feed_identifiers:
            for identifier in feed_identifiers:
                self._validate_feed_exists(identifier)

        # Parse date filters - use timezone-aware datetimes for recurring_ical_events
        if start_date:
            start_dt = self._normalize_datetime(start_date)
        else:
            start_dt = datetime.now(UTC)

        if end_date:
            end_dt = self._normalize_datetime(end_date)
        else:
            end_dt = start_dt + timedelta(days=7)
        
        # Determine which feeds to query
        feeds_to_query = []
        if feed_identifiers:
            for identifier in feed_identifiers:
                feed = self._find_feed(identifier)
                if feed:
                    feeds_to_query.append(feed)
        else:
            feeds_to_query = list(self.feeds.values())
        
        with self._lock:
            for feed in feeds_to_query:
                if not feed.calendar:
                    continue
                
                try:
                    # Use recurring_ical_events to expand recurring events
                    # This will give us individual occurrences instead of just the RRULE
                    expanded_events = recurring_ical_events.of(feed.calendar).between(
                        start_dt,
                        end_dt
                    )
                    
                    for event in expanded_events:
                        event_dict = self._event_to_dict(event, feed)
                        events.append(event_dict)
                        
                except Exception as e:
                    logger.warning(f"Failed to expand recurring events for feed {feed.name}: {e}")
                    # Fallback to non-recurring event processing
                    for component in feed.calendar.walk():
                        if component.name == "VEVENT":
                            event_start = component.get('DTSTART')
                            if event_start and hasattr(event_start, 'dt'):
                                event_dt = self._normalize_datetime(event_start.dt)
                                
                                # Apply date filters
                                if start_dt and event_dt and event_dt < start_dt:
                                    continue
                                if end_dt and event_dt and event_dt > end_dt:
                                    continue
                            
                            event_dict = self._event_to_dict(component, feed)
                            events.append(event_dict)
        
        # Sort by start date
        events.sort(key=lambda x: x.get('start') or '')
        
        # Apply pagination
        if limit is not None:
            end_index = offset + limit
            events = events[offset:end_index]
        elif offset > 0:
            events = events[offset:]
        
        return events
    
    def get_today_events(self, feed_identifiers: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Get events for today from specified feeds or all feeds"""
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = today + timedelta(days=1)
        
        return self.get_events(
            start_date=today.isoformat(),
            end_date=tomorrow.isoformat(),
            feed_identifiers=feed_identifiers
        )
    
    def get_upcoming_events(self, count: int = 10, feed_identifiers: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Get upcoming events from specified feeds or all feeds"""
        now = datetime.now(UTC)
        future_events = []
        
        feeds_to_query = []
        if feed_identifiers:
            for identifier in feed_identifiers:
                feed = self._find_feed(identifier)
                if feed:
                    feeds_to_query.append(feed)
        else:
            feeds_to_query = list(self.feeds.values())
        
        with self._lock:
            for feed in feeds_to_query:
                if not feed.calendar:
                    continue
                
                for component in feed.calendar.walk():
                    if component.name == "VEVENT":
                        event_start = component.get('DTSTART')
                        if event_start and hasattr(event_start, 'dt'):
                            event_dt = self._normalize_datetime(event_start.dt)
                            if event_dt and event_dt >= now:
                                event_dict = self._event_to_dict(component, feed)
                                future_events.append((event_dt, event_dict))
        
        # Sort by start date and return requested count
        future_events.sort(key=lambda x: x[0])
        return [event for _, event in future_events[:count]]
    
    @cache_aside(CacheConfig(ttl=CacheTTL.CALENDAR_EVENTS, key_prefix="ical:search"))
    def search_events(self, query: str, feed_identifiers: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Search events by title, description, location, or feed name"""
        if not query:
            return []
        
        query_lower = query.lower()
        # Also handle common variations
        query_variations = [
            query_lower,
            query_lower.replace(' ', '_'),  # Handle underscore vs space
            query_lower.replace('_', ' '),  # Handle space vs underscore
            query_lower.replace('-', ' '),  # Handle hyphen vs space
        ]
        
        matching_events = []
        
        # If no specific feeds, check if query matches a feed name first
        if not feed_identifiers:
            # Check if query matches any feed name
            for feed in self.feeds.values():
                feed_name_lower = feed.name.lower()
                if any(var in feed_name_lower or feed_name_lower in var for var in query_variations):
                    # Query matches a feed name, search only in that feed
                    if feed_identifiers is None:
                        feed_identifiers = []
                    feed_identifiers.append(feed.id)
        
        feeds_to_query = []
        if feed_identifiers:
            for identifier in feed_identifiers:
                feed = self._find_feed(identifier)
                if feed:
                    feeds_to_query.append(feed)
        else:
            feeds_to_query = list(self.feeds.values())
        
        with self._lock:
            for feed in feeds_to_query:
                if not feed.calendar:
                    continue
                
                # Also check if query matches feed name (for cross-feed search)
                feed_name_matches = any(var in feed.name.lower() for var in query_variations)
                
                for component in feed.calendar.walk():
                    if component.name == "VEVENT":
                        summary = str(component.get('SUMMARY', '')).lower()
                        description = str(component.get('DESCRIPTION', '')).lower()
                        location = str(component.get('LOCATION', '')).lower()
                        
                        # Check if any variation matches
                        matches = any(
                            var in summary or var in description or var in location
                            for var in query_variations
                        )
                        
                        # Include all events from matching feed names or matching event content
                        if matches or feed_name_matches:
                            event_dict = self._event_to_dict(component, feed)
                            matching_events.append(event_dict)
        
        matching_events.sort(key=lambda x: x.get('start') or '')
        return matching_events
    
    def get_event_by_uid(self, uid: str, feed_identifier: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get a specific event by its UID"""
        feeds_to_query = []
        if feed_identifier:
            feed = self._find_feed(feed_identifier)
            if feed:
                feeds_to_query = [feed]
        else:
            feeds_to_query = list(self.feeds.values())
        
        with self._lock:
            for feed in feeds_to_query:
                if not feed.calendar:
                    continue
                
                for component in feed.calendar.walk():
                    if component.name == "VEVENT":
                        if str(component.get('UID', '')) == uid:
                            return self._event_to_dict(component, feed)
        
        return None
    
    @cache_aside(CacheConfig(ttl=CacheTTL.CALENDAR_INFO, key_prefix="ical:info"))
    def get_calendar_info(self) -> Dict[str, Any]:
        """Get information about all cached calendars"""
        info = {
            "status": "loaded",
            "total_feeds": len(self.feeds),
            "feeds": []
        }
        
        with self._lock:
            for feed_id, feed in self.feeds.items():
                if feed.calendar:
                    event_count = sum(1 for comp in feed.calendar.walk() if comp.name == "VEVENT")
                    
                    # Try to get the calendar name from the actual calendar data
                    cal_name = feed.name
                    cal_display_name = None
                    if hasattr(feed.calendar, 'get'):
                        cal_display_name = feed.calendar.get('X-WR-CALNAME')
                        if not cal_display_name:
                            cal_display_name = feed.calendar.get('NAME')
                    
                    feed_info = {
                        "feed_id": feed.id,
                        "feed_name": feed.name,
                        "calendar_name": str(cal_display_name) if cal_display_name else feed.name,
                        "description": str(feed.calendar.get('X-WR-CALDESC', '')) if hasattr(feed.calendar, 'get') else '',
                        "timezone": str(feed.calendar.get('X-WR-TIMEZONE', 'UTC')) if hasattr(feed.calendar, 'get') else 'UTC',
                        "feed_url": feed.url,
                        "event_count": event_count,
                        "last_fetch": feed.last_fetch.isoformat() if feed.last_fetch else None,
                        "status": "loaded",
                        "error": feed.error
                    }
                else:
                    feed_info = {
                        "feed_url": feed.url,
                        "feed_name": feed.name,
                        "feed_id": feed.id,
                        "status": "not_loaded" if not feed.error else "error",
                        "error": feed.error,
                        "last_fetch": feed.last_fetch.isoformat() if feed.last_fetch else None
                    }
                
                info["feeds"].append(feed_info)
        
        info["refresh_interval_minutes"] = self.refresh_interval // 60
        
        return info
    
    def add_feed(self, url: str, name: Optional[str] = None) -> Dict[str, Any]:
        """Add a new named feed URL and fetch it"""
        # Validate URL
        self._validate_url(url)
        
        for feed in self.feeds.values():
            if feed.url == url:
                return {
                    "status": "already_exists",
                    "feed_url": url,
                    "feed_name": feed.name,
                    "feed_id": feed.id
                }
        
        feed = CalendarFeed(url, name)
        self.feeds[feed.id] = feed
        
        return self._refresh_single_calendar(feed)
    
    def remove_feed(self, feed_identifier: str) -> Dict[str, Any]:
        """Remove a feed by URL, name, or ID"""
        # Validate feed exists
        feed = self._validate_feed_exists(feed_identifier)
        
        if feed:
            with self._lock:
                del self.feeds[feed.id]
            
            return {
                "status": "removed",
                "feed_url": feed.url,
                "feed_name": feed.name,
                "feed_id": feed.id
            }
        
        return {
            "status": "not_found",
            "feed_identifier": feed_identifier
        }
    
    def list_feeds(self) -> List[Dict[str, str]]:
        """List all configured feeds with their names and IDs"""
        feeds_list = []
        for feed in self.feeds.values():
            feed_entry = {
                "id": feed.id,
                "name": feed.name,
                "url": feed.url,
                "status": "loaded" if feed.calendar else ("error" if feed.error else "not_loaded")
            }
            
            # Add calendar display name if available
            if feed.calendar and hasattr(feed.calendar, 'get'):
                cal_display_name = feed.calendar.get('X-WR-CALNAME')
                if cal_display_name:
                    feed_entry["calendar_name"] = str(cal_display_name)
            
            feeds_list.append(feed_entry)
        
        return feeds_list
    
    def stop(self):
        """Stop the automatic refresh timer"""
        if self._refresh_timer:
            self._refresh_timer.cancel()
            self._refresh_timer = None
    
    def _register_mcp_tools(self):
        """Register MCP tools for this service"""
        # Register tool methods with MCP
        # NOTE: Getter tools commented out - use resources instead for read-only data
        # Resources provide: calendar-info, today-events, upcoming-events
        # Commented tools: ical_get_events, ical_list_feeds
        
        # self.mcp.tool(
        #     name="ical_get_events",
        #     description="Retrieve calendar events within a specified date range from configured iCalendar feeds. Parameters: start_date (YYYY-MM-DD format, defaults to today), end_date (YYYY-MM-DD format, defaults to 7 days from start), calendar_name (optional filter by specific calendar). Returns: Dictionary with events array containing title, start/end times, location, description, and source feed. Use for: Getting events for specific date ranges, filtering by calendar, or retrieving all events across calendars.",
        #     annotations={"title": "Get Calendar Events"}
        # )(self.get_events_for_mcp)
        
        self.mcp.tool(
            name="add_calendar_feed",
            description="""Add a new iCalendar feed URL to the list of monitored calendars.

## Parameters
• url: iCalendar feed URL (required)
• name: Friendly name for the feed (optional)

## Returns
Success/error status with feed details

## Use Cases
• Subscribe to Google Calendar feeds
• Add Outlook calendar subscriptions  
• Monitor other iCal sources

## Related Tools
• Call `get_calendar_feeds` to see existing feeds before adding
• Call `refresh_calendar_feeds` after adding to sync immediately""",
            title="Add Calendar Feed",
            annotations={"title": "Add Calendar Feed"}
        )(self.add_feed)
        
        self.mcp.tool(
            name="remove_calendar_feed",
            description="""Remove an iCalendar feed from the list of monitored calendars.

## Parameters
• url: The feed URL to remove (required)
  - Call `get_calendar_feeds` to see available feed URLs

## Returns
Success/error status

## Use Cases
• Unsubscribe from calendar feeds no longer needed
• Clean up old or broken feed subscriptions""",
            title="Remove Calendar Feed",
            annotations={"title": "Remove Calendar Feed"}
        )(self.remove_feed_for_mcp)
        
        self.mcp.tool(
            name="refresh_calendar_feeds",
            description="""Force refresh all calendar feeds to get the latest events.

## Parameters
None required

## Returns
• Success status
• Number of feeds refreshed
• Total events found

## Use Cases
• Get immediate updates from all calendar sources
• Sync after adding new feeds
• Refresh before important queries""",
            title="Refresh Calendar Feeds",
            annotations={"title": "Refresh Calendar Feeds"}
        )(self.refresh_feeds_for_mcp)
        
        # Commented out - use resources instead for read-only data
        # self.mcp.tool(
        #     name="ical_list_feeds",
        #     description="List all currently configured iCalendar feeds. No parameters required. Returns: Array of feed objects with name, URL, last updated time, and event count. Use to: See which calendar sources are being monitored and their status.",
        #     annotations={"title": "List Calendar Feeds"}
        # )(self.list_feeds_for_mcp)
        
        # Register tools (Claude cannot use resources, only tools)
        self.mcp.tool(
            name="get_calendar_info",
            description=f"""Get information about all configured calendar feeds (cached for {CacheTTL.CALENDAR_INFO//60} minutes).

## Returns
• Status of each feed
• Event counts per feed
• Last update times
• Total number of feeds
• Refresh interval settings

## Use Cases
• Check feed health and status
• See when feeds were last updated
• Monitor feed event counts

## Caching
• Feed information cached for {CacheTTL.CALENDAR_INFO//60} minutes
• Feed metadata doesn't change frequently""",
            title="Calendar Information",
            annotations={"title": "Calendar Information"}
        )(self.get_calendar_info_resource)
        
        self.mcp.tool(
            name="get_today_events",
            description=f"""Get all calendar events happening today across all configured feeds (cached for {CacheTTL.CALENDAR_EVENTS//60} minutes).

## Returns
• List of today's events
• Event times and durations
• Event locations and descriptions
• Feed source for each event

## Use Cases
• Daily schedule overview
• Check for conflicts today
• Plan the current day

## Related Tools
• Use `get_tomorrow_events` for next day planning
• Use `get_calendar_conflicts` to find overlapping events

## Caching
• Event data cached for {CacheTTL.CALENDAR_EVENTS//60} minutes
• Balances freshness with performance for calendar data""",
            title="Today's Events",
            annotations={"title": "Today's Events"}
        )(self.get_today_events_resource)
        
        self.mcp.tool(
            name="get_upcoming_events",
            description=f"""Get the next 20 upcoming calendar events across all configured feeds (cached for {CacheTTL.CALENDAR_EVENTS//60} minutes).

## Returns
• Next 20 events sorted by start time
• Event details (title, time, location)
• Feed source for each event

## Use Cases
• Preview upcoming schedule
• Long-term planning
• Check future availability

## Related Tools
• Use `get_week_events` for current week only
• Use `get_month_events` for current month view

## Caching
• Event data cached for {CacheTTL.CALENDAR_EVENTS//60} minutes
• Recent events refreshed periodically""",
            title="Upcoming Events",
            annotations={"title": "Upcoming Events"}
        )(self.get_upcoming_events_resource)
        
        # Parameterized queries as tools (Claude can only get static resources)
        self.mcp.tool(
            name="get_events_on_date",
            description=f"""Get all calendar events on a specific date (cached for {CacheTTL.CALENDAR_EVENTS//60} minutes).

## Parameters
• date: Date in YYYY-MM-DD format (required)
• feed: Specific feed name to filter (optional)
  - Call `get_calendar_feeds` to see available feed names

## Returns
All events for the specified date

## Use Cases
• Check schedule for a specific day
• Plan for future dates
• Review past events

## Caching
• Event data cached for {CacheTTL.CALENDAR_EVENTS//60} minutes
• Date-specific queries use cached event data""",
            title="Get Events on Date",
            annotations={"title": "Get Events on Date"}
        )(self.get_events_on_date_resource)
        
        self.mcp.tool(
            name="get_events_between_dates",
            description=f"""Get all calendar events between two dates (cached for {CacheTTL.CALENDAR_EVENTS//60} minutes).

## Parameters
• start_date: Start date in YYYY-MM-DD format (required)
• end_date: End date in YYYY-MM-DD format (required)
• feed: Specific feed name to filter (optional)
  - Call `get_calendar_feeds` to see available feed names

## Returns
All events between the specified dates

## Use Cases
• Get events for a custom date range
• Plan vacations or trips
• Review activity for a period

## Caching
• Event data cached for {CacheTTL.CALENDAR_EVENTS//60} minutes
• Range queries use cached event data""",
            title="Get Events Between Dates",
            annotations={"title": "Get Events Between Dates"}
        )(self.get_events_between_resource)
        
        self.mcp.tool(
            name="get_events_after_date",
            description=f"""Get all calendar events after a specific date (next 30 days) (cached for {CacheTTL.CALENDAR_EVENTS//60} minutes).

## Parameters
• date: Start date in YYYY-MM-DD format (required)
• feed: Specific feed name to filter (optional)
  - Call `get_calendar_feeds` to see available feed names

## Returns
Events for the next 30 days after the specified date

## Use Cases
• Look ahead from a specific date
• Plan future activities
• Check upcoming availability

## Caching
• Event data cached for {CacheTTL.CALENDAR_EVENTS//60} minutes
• Future event queries use cached data""",
            title="Get Events After Date",
            annotations={"title": "Get Events After Date"}
        )(self.get_events_after_resource)
        
        self.mcp.tool(
            name="search_calendar_events",
            description=f"""Search for calendar events by text (cached for {CacheTTL.CALENDAR_EVENTS//60} minutes).

## Parameters
• query: Search text (required)
• feed: Specific feed name to filter (optional)
  - Call `get_calendar_feeds` to see available feed names

## Returns
Events matching the search query in:
• Title
• Description  
• Location

## Use Cases
• Find specific meetings or events
• Search for events at a location
• Locate events with specific keywords

## Caching
• Search results cached for {CacheTTL.CALENDAR_EVENTS//60} minutes
• Searches performed on cached event data""",
            title="Search Calendar Events",
            annotations={"title": "Search Calendar Events"}
        )(self.search_events_resource)
        
        # Tools for constant values (for LLM discovery)
        self.mcp.tool(
            name="get_calendar_feeds",
            description="""Get list of configured calendar feed names and URLs.

## Returns
• Feed names
• Feed URLs
• Feed configuration details

## Use Cases
• See available feeds before filtering
• Get feed names for other tool parameters
• Check feed URLs for removal""",
            title="Calendar Feeds",
            annotations={"title": "Calendar Feeds"}
        )(self.get_feeds_list_resource)
        
        # Additional tools for common queries
        self.mcp.tool(
            name="get_week_events",
            description=f"""Get all calendar events for the current week (cached for {CacheTTL.CALENDAR_EVENTS//60} minutes).

## Returns
• All events from Monday to Sunday
• Organized by day
• Event times and details

## Use Cases
• Weekly planning
• Week at a glance
• Short-term scheduling

## Related Tools
• Use `get_today_events` for today only
• Use `get_month_events` for broader view

## Caching
• Event data cached for {CacheTTL.CALENDAR_EVENTS//60} minutes
• Week view uses cached event data""",
            title="This Week's Events",
            annotations={"title": "This Week's Events"}
        )(self.get_week_events_resource)
        
        self.mcp.tool(
            name="get_month_events",
            description=f"""Get all calendar events for the current month (cached for {CacheTTL.CALENDAR_EVENTS//60} minutes).

## Returns
• All events for the current month
• Organized by date
• Event counts per day

## Use Cases
• Monthly overview
• Long-term planning
• Month-end reviews

## Related Tools
• Use `get_week_events` for current week
• Use `get_events_between_dates` for custom ranges

## Caching
• Event data cached for {CacheTTL.CALENDAR_EVENTS//60} minutes
• Month view uses cached event data""",
            title="This Month's Events",
            annotations={"title": "This Month's Events"}
        )(self.get_month_events_resource)
        
        self.mcp.tool(
            name="get_tomorrow_events",
            description=f"""Get all calendar events for tomorrow (cached for {CacheTTL.CALENDAR_EVENTS//60} minutes).

## Returns
• Tomorrow's complete schedule
• Event times and details
• Feed sources

## Use Cases
• Next day preparation
• Evening planning for tomorrow
• Advance notifications

## Related Tools
• Use `get_today_events` for current day
• Use `get_events_on_date` for other specific dates

## Caching
• Event data cached for {CacheTTL.CALENDAR_EVENTS//60} minutes
• Tomorrow's events from cached data""",
            title="Tomorrow's Events",
            annotations={"title": "Tomorrow's Events"}
        )(self.get_tomorrow_events_resource)
        
        self.mcp.tool(
            name="get_calendar_conflicts",
            description=f"""Get overlapping or conflicting events in the next 7 days (cached for {CacheTTL.CALENDAR_EVENTS//60} minutes).

## Returns
• List of conflicting event pairs
• Overlap duration
• Conflict details

## Use Cases
• Identify scheduling conflicts
• Find double-booked time slots
• Clean up calendar overlaps

## Related Tools
• Use `get_week_events` to see all events
• Use `get_today_events` to check today's conflicts

## Caching
• Event data cached for {CacheTTL.CALENDAR_EVENTS//60} minutes
• Conflict detection on cached events""",
            title="Calendar Conflicts",
            annotations={"title": "Calendar Conflicts"}
        )(self.get_conflicts_resource)
        
        self.mcp.tool(
            name="analyze_calendar_conflicts",
            description=f"""Analyze calendar conflicts with severity levels and advanced filtering (cached for {CacheTTL.CALENDAR_EVENTS//60} minutes).

## Parameters
• days_ahead: Number of days to analyze (default: "7")
• include_all_day: Include all-day events - "true"/"false" (default: "false")
• min_overlap_minutes: Minimum overlap to report (default: "0")
• severity_threshold: Filter by severity - "all", "high", "medium", "low" (default: "all")

## Returns
• Detailed conflict analysis with severity levels
• Conflicts grouped by severity (high/medium/low)
• Statistics and recommendations
• Timezone information (UTC)

## Severity Levels
• High: Exact overlaps, >60min overlap, or >30min time conflicts
• Medium: Partial overlaps between 15-60 minutes
• Low: All-day overlaps, <15min overlaps, or tentative events

## Use Cases
• Focus on high-severity conflicts only
• Filter out minor overlaps (e.g., min_overlap_minutes="30")
• Exclude all-day events from analysis
• Get scheduling recommendations

## Examples
• analyze_calendar_conflicts("30", "false", "15", "high") - Next 30 days, high severity only, min 15min overlap
• analyze_calendar_conflicts("7", "true", "0", "all") - Next week, include all conflicts

## Related Tools
• Use `get_calendar_conflicts` for simple conflict list
• Use `get_week_events` to see all events
• Use `search_calendar_events` to find specific events

## Caching
• Event data cached for {CacheTTL.CALENDAR_EVENTS//60} minutes
• Advanced conflict analysis on cached events""",
            title="Analyze Calendar Conflicts",
            annotations={"title": "Analyze Calendar Conflicts"}
        )(self.analyze_conflicts_for_mcp)
    
    # MCP Tool Methods
    def get_events_for_mcp(self, start_date: Optional[str] = None, end_date: Optional[str] = None, 
                           calendar_name: Optional[str] = None) -> Dict[str, Any]:
        """MCP tool wrapper for get_events"""
        try:
            # Parse dates
            if start_date:
                start = datetime.strptime(start_date, '%Y-%m-%d').date()
            else:
                start = date.today()
            
            if end_date:
                end = datetime.strptime(end_date, '%Y-%m-%d').date()
            else:
                end = start + timedelta(days=7)
            
            # If calendar_name is provided, pass it as a list of identifiers
            feed_identifiers = [calendar_name] if calendar_name else None
            
            events = self.get_events(
                start_date=start,
                end_date=end,
                feed_identifiers=feed_identifiers
            )
            
            return {"events": events, "count": len(events)}
        except Exception as e:
            logger.error(f"Error getting events: {e}")
            return {"error": str(e)}
    
    def remove_feed_for_mcp(self, url: str) -> Dict[str, Any]:
        """MCP tool wrapper for remove_feed"""
        try:
            result = self.remove_feed(url)
            if result['status'] == 'removed':
                return {"success": True, "message": f"Removed calendar feed: {url}"}
            else:
                return {"error": f"Feed not found: {url}"}
        except Exception as e:
            logger.error(f"Error removing feed: {e}")
            return {"error": str(e)}
    
    def refresh_feeds_for_mcp(self) -> Dict[str, Any]:
        """MCP tool wrapper for refresh_all_calendars"""
        try:
            self.refresh_all_calendars()
            info = self.get_calendar_info()
            return {"success": True, "message": "All calendar feeds refreshed", "feeds": info}
        except Exception as e:
            logger.error(f"Error refreshing feeds: {e}")
            return {"error": str(e)}
    
    def list_feeds_for_mcp(self) -> Dict[str, Any]:
        """MCP tool wrapper for get_calendar_info"""
        try:
            info = self.get_calendar_info()
            return {"feeds": info}
        except Exception as e:
            logger.error(f"Error listing feeds: {e}")
            return {"error": str(e)}
    
    # Resource Methods
    def get_calendar_info_resource(self) -> Dict[str, Any]:
        """Resource providing calendar information"""
        return self.get_calendar_info()
    
    def get_today_events_resource(self) -> List[Dict[str, Any]]:
        """Resource providing today's events"""
        return self.get_today_events()
    
    def get_upcoming_events_resource(self) -> List[Dict[str, Any]]:
        """Resource providing upcoming events"""
        return self.get_upcoming_events(count=20)
    
    def get_events_on_date_resource(self, date: str, feed: Optional[str] = None) -> Dict[str, Any]:
        """Resource providing events on a specific date, optionally filtered by feed"""
        try:
            target_date = datetime.strptime(date, '%Y-%m-%d').replace(tzinfo=UTC)
            next_day = target_date + timedelta(days=1)
            feed_identifiers = [feed] if feed else None
            events = self.get_events(
                start_date=target_date.isoformat(),
                end_date=next_day.isoformat(),
                feed_identifiers=feed_identifiers
            )
            result = {
                "date": date,
                "events": events,
                "events_count": len(events)
            }
            if feed:
                result["feed"] = feed
            return result
        except Exception as e:
            logger.error(f"Error getting events for date {date}: {e}")
            return {"error": str(e), "date": date}
    
    def get_events_between_resource(self, start_date: str, end_date: str, feed: Optional[str] = None) -> Dict[str, Any]:
        """Resource providing events between two dates, optionally filtered by feed"""
        try:
            start = datetime.strptime(start_date, '%Y-%m-%d').replace(tzinfo=UTC)
            end = datetime.strptime(end_date, '%Y-%m-%d').replace(tzinfo=UTC)
            feed_identifiers = [feed] if feed else None
            events = self.get_events(
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                feed_identifiers=feed_identifiers
            )
            result = {
                "start_date": start_date,
                "end_date": end_date,
                "events": events,
                "events_count": len(events)
            }
            if feed:
                result["feed"] = feed
            return result
        except Exception as e:
            logger.error(f"Error getting events between {start_date} and {end_date}: {e}")
            return {"error": str(e), "start_date": start_date, "end_date": end_date}
    
    def get_events_after_resource(self, date: str, feed: Optional[str] = None) -> Dict[str, Any]:
        """Resource providing events after a specific date, optionally filtered by feed"""
        try:
            start = datetime.strptime(date, '%Y-%m-%d').replace(tzinfo=UTC)
            # Get events for the next 30 days
            end = start + timedelta(days=30)
            feed_identifiers = [feed] if feed else None
            events = self.get_events(
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                feed_identifiers=feed_identifiers
            )
            result = {
                "after_date": date,
                "events": events,
                "events_count": len(events),
                "note": "Shows events for 30 days after the specified date"
            }
            if feed:
                result["feed"] = feed
            return result
        except Exception as e:
            logger.error(f"Error getting events after {date}: {e}")
            return {"error": str(e), "after_date": date}
    
    def search_events_resource(self, query: str, feed: Optional[str] = None) -> Dict[str, Any]:
        """Resource for searching calendar events, optionally filtered by feed"""
        try:
            feed_identifiers = [feed] if feed else None
            events = self.search_events(query, feed_identifiers=feed_identifiers)
            result = {
                "query": query,
                "events": events,
                "events_count": len(events)
            }
            if feed:
                result["feed"] = feed
            return result
        except Exception as e:
            logger.error(f"Error searching for events with query '{query}': {e}")
            return {"error": str(e), "query": query}
    
    def analyze_conflicts_for_mcp(self, 
                                  days_ahead: str = "7",
                                  include_all_day: str = "false",
                                  min_overlap_minutes: str = "0",
                                  severity_threshold: str = "all") -> Dict[str, Any]:
        """MCP wrapper for analyze_calendar_conflicts with string to type conversion"""
        try:
            # Convert string parameters to appropriate types
            days = int(days_ahead)
            include_all = include_all_day.lower() == "true"
            min_overlap = int(min_overlap_minutes)
            
            # Validate severity threshold
            valid_thresholds = ["all", "high", "medium", "low"]
            if severity_threshold not in valid_thresholds:
                return {
                    "error": f"Invalid severity_threshold: {severity_threshold}",
                    "help": f"Must be one of: {', '.join(valid_thresholds)}",
                    "examples": [
                        "all - Show all conflicts",
                        "high - Only high severity",
                        "medium - Medium and high",
                        "low - All severities"
                    ]
                }
            
            # Call the analysis method
            return self.analyze_calendar_conflicts(
                days_ahead=days,
                include_all_day=include_all,
                min_overlap_minutes=min_overlap,
                severity_threshold=severity_threshold
            )
            
        except ValueError as e:
            return {
                "error": str(e),
                "help": "Check parameter formats",
                "parameters": {
                    "days_ahead": "Number of days (e.g., '7', '30')",
                    "include_all_day": "'true' or 'false'",
                    "min_overlap_minutes": "Minimum overlap in minutes (e.g., '15', '30')",
                    "severity_threshold": "One of: all, high, medium, low"
                }
            }
        except Exception as e:
            logger.error(f"Error analyzing conflicts: {e}")
            return {"error": str(e)}
    
    def get_feeds_list_resource(self) -> Dict[str, Any]:
        """Resource providing list of configured calendar feeds"""
        feeds_list = []
        for feed_id, feed in self.feeds.items():
            feeds_list.append({
                "name": feed.name,
                "id": feed_id,
                "url": feed.url,
                "event_count": len(list(feed.calendar.walk())) if feed.calendar else 0,
                "last_updated": feed.last_fetch.isoformat() if feed.last_fetch else None
            })
        
        return {
            "feeds": feeds_list,
            "feed_count": len(feeds_list),
            "usage": "Use feed name in ical://feed/{feed_name}/events to get events from a specific feed"
        }
    
    def get_week_events_resource(self) -> Dict[str, Any]:
        """Get all events for the current week"""
        now = datetime.now(UTC)
        # Get start of week (Monday)
        start_of_week = now - timedelta(days=now.weekday())
        start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
        # Get end of week (Sunday)
        end_of_week = start_of_week + timedelta(days=7)
        
        events = self.get_events(
            start_date=start_of_week.isoformat(),
            end_date=end_of_week.isoformat()
        )
        
        return {
            "week_start": start_of_week.isoformat(),
            "week_end": end_of_week.isoformat(),
            "events": events,
            "events_count": len(events)
        }
    
    def get_month_events_resource(self) -> Dict[str, Any]:
        """Get all events for the current month"""
        now = datetime.now(UTC)
        # Get first day of month
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # Get first day of next month
        if now.month == 12:
            end_of_month = start_of_month.replace(year=now.year + 1, month=1)
        else:
            end_of_month = start_of_month.replace(month=now.month + 1)
        
        events = self.get_events(
            start_date=start_of_month.isoformat(),
            end_date=end_of_month.isoformat()
        )
        
        return {
            "month": now.strftime("%B %Y"),
            "month_start": start_of_month.isoformat(),
            "month_end": end_of_month.isoformat(),
            "events": events,
            "events_count": len(events)
        }
    
    def get_tomorrow_events_resource(self) -> Dict[str, Any]:
        """Get all events for tomorrow"""
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = today + timedelta(days=1)
        day_after = tomorrow + timedelta(days=1)
        
        events = self.get_events(
            start_date=tomorrow.isoformat(),
            end_date=day_after.isoformat()
        )
        
        return {
            "date": tomorrow.date().isoformat(),
            "events": events,
            "events_count": len(events)
        }
    
    def get_conflicts_resource(self, include_all_day: bool = False) -> Dict[str, Any]:
        """Find overlapping events in the next 7 days
        
        Args:
            include_all_day: Whether to include all-day events in conflict detection
        """
        now = datetime.now(UTC)
        week_later = now + timedelta(days=7)
        
        events = self.get_events(
            start_date=now.isoformat(),
            end_date=week_later.isoformat()
        )
        
        conflicts = []
        for i, event1 in enumerate(events):
            # Check if event1 is all-day
            is_all_day1 = self._is_all_day_event(event1)
            if not include_all_day and is_all_day1:
                continue  # Skip all-day events if not included
                
            for event2 in events[i+1:]:
                # Check if event2 is all-day
                is_all_day2 = self._is_all_day_event(event2)
                if not include_all_day and is_all_day2:
                    continue  # Skip all-day events if not included
                
                # Skip if both are all-day events (they don't really conflict)
                if is_all_day1 and is_all_day2:
                    continue
                    
                # Check if events overlap
                start1 = self._normalize_datetime(event1.get('start'))
                end1 = self._normalize_datetime(event1.get('end'))
                start2 = self._normalize_datetime(event2.get('start'))
                end2 = self._normalize_datetime(event2.get('end'))
                
                if start1 and end1 and start2 and end2:
                    # Check for overlap
                    if (start1 < end2 and end1 > start2):
                        conflicts.append({
                            "event1": {
                                "summary": event1.get('summary'),
                                "start": event1.get('start'),
                                "end": event1.get('end'),
                                "all_day": is_all_day1
                            },
                            "event2": {
                                "summary": event2.get('summary'),
                                "start": event2.get('start'),
                                "end": event2.get('end'),
                                "all_day": is_all_day2
                            },
                            "conflict_type": "all_day_overlap" if (is_all_day1 or is_all_day2) else "time_overlap"
                        })
        
        return {
            "period": f"{now.date().isoformat()} to {week_later.date().isoformat()}",
            "conflicts": conflicts,
            "conflict_count": len(conflicts),
            "include_all_day": include_all_day,
            "note": "All-day events excluded from conflicts by default. Set include_all_day=true to include them."
        }
    
    def analyze_calendar_conflicts(self, 
                                   days_ahead: int = 7,
                                   include_all_day: bool = False,
                                   min_overlap_minutes: int = 0,
                                   severity_threshold: str = "all") -> Dict[str, Any]:
        """
        Analyze calendar conflicts with detailed filtering and severity levels
        
        Args:
            days_ahead: Number of days to look ahead (default 7)
            include_all_day: Include all-day events in conflict detection
            min_overlap_minutes: Minimum overlap in minutes to consider a conflict
            severity_threshold: Filter by severity - "all", "high", "medium", "low"
        
        Returns:
            Detailed conflict analysis with severity levels and statistics
        """
        now = datetime.now(UTC)
        end_date = now + timedelta(days=days_ahead)
        
        events = self.get_events(
            start_date=now.isoformat(),
            end_date=end_date.isoformat()
        )
        
        conflicts = []
        
        for i, event1 in enumerate(events):
            # Check if event1 is all-day
            is_all_day1 = self._is_all_day_event(event1)
            if not include_all_day and is_all_day1:
                continue
                
            for event2 in events[i+1:]:
                # Check if event2 is all-day
                is_all_day2 = self._is_all_day_event(event2)
                if not include_all_day and is_all_day2:
                    continue
                
                # Skip if both are all-day events
                if is_all_day1 and is_all_day2:
                    continue
                    
                # Check for overlap
                conflict_info = self._analyze_event_overlap(event1, event2, is_all_day1, is_all_day2)
                
                if conflict_info and conflict_info['overlap_minutes'] >= min_overlap_minutes:
                    # Add severity level
                    conflict_info['severity'] = self._determine_conflict_severity(
                        conflict_info, event1, event2
                    )
                    
                    # Filter by severity threshold
                    if self._meets_severity_threshold(conflict_info['severity'], severity_threshold):
                        conflicts.append(conflict_info)
        
        # Group conflicts by severity
        conflicts_by_severity = {
            'high': [c for c in conflicts if c['severity'] == 'high'],
            'medium': [c for c in conflicts if c['severity'] == 'medium'],
            'low': [c for c in conflicts if c['severity'] == 'low']
        }
        
        # Calculate statistics
        stats = {
            'total_events': len(events),
            'events_with_conflicts': len(set(
                c['event1']['id'] for c in conflicts
            ).union(set(c['event2']['id'] for c in conflicts))),
            'conflict_percentage': round(
                (len(conflicts) / max(len(events), 1)) * 100, 1
            ) if events else 0
        }
        
        return {
            'analysis_period': {
                'start': now.isoformat(),
                'end': end_date.isoformat(),
                'days': days_ahead,
                'timezone': 'UTC'
            },
            'filters': {
                'include_all_day': include_all_day,
                'min_overlap_minutes': min_overlap_minutes,
                'severity_threshold': severity_threshold
            },
            'conflicts': conflicts,
            'conflicts_by_severity': conflicts_by_severity,
            'summary': {
                'total_conflicts': len(conflicts),
                'high_severity': len(conflicts_by_severity['high']),
                'medium_severity': len(conflicts_by_severity['medium']),
                'low_severity': len(conflicts_by_severity['low'])
            },
            'statistics': stats,
            'recommendations': self._generate_conflict_recommendations(conflicts_by_severity)
        }
    
    def _analyze_event_overlap(self, event1: Dict, event2: Dict, 
                               is_all_day1: bool, is_all_day2: bool) -> Optional[Dict]:
        """Analyze overlap between two events and return conflict details"""
        start1 = self._normalize_datetime(event1.get('start'))
        end1 = self._normalize_datetime(event1.get('end'))
        start2 = self._normalize_datetime(event2.get('start'))
        end2 = self._normalize_datetime(event2.get('end'))
        
        if not all([start1, end1, start2, end2]):
            return None
            
        # Check for overlap
        if not (start1 < end2 and end1 > start2):
            return None
            
        # Calculate overlap duration
        overlap_start = max(start1, start2)
        overlap_end = min(end1, end2)
        overlap_duration = overlap_end - overlap_start
        
        # Ensure we don't have negative overlap (shouldn't happen if overlap check is correct)
        if overlap_duration.total_seconds() <= 0:
            return None
            
        overlap_minutes = max(0, int(overlap_duration.total_seconds() / 60))
        
        # Determine conflict type
        if is_all_day1 or is_all_day2:
            conflict_type = 'all_day_overlap'
        elif start1 == start2 and end1 == end2:
            conflict_type = 'exact_overlap'
        elif start1 == start2 or end1 == end2:
            conflict_type = 'partial_overlap'
        else:
            conflict_type = 'time_overlap'
        
        return {
            'event1': {
                'id': event1.get('uid', event1.get('summary', 'unknown')),
                'summary': event1.get('summary'),
                'start': event1.get('start'),
                'end': event1.get('end'),
                'all_day': is_all_day1,
                'feed': event1.get('feed_name', 'unknown')
            },
            'event2': {
                'id': event2.get('uid', event2.get('summary', 'unknown')),
                'summary': event2.get('summary'),
                'start': event2.get('start'),
                'end': event2.get('end'),
                'all_day': is_all_day2,
                'feed': event2.get('feed_name', 'unknown')
            },
            'overlap': {
                'start': overlap_start.isoformat(),
                'end': overlap_end.isoformat(),
                'duration_minutes': overlap_minutes
            },
            'conflict_type': conflict_type,
            'overlap_minutes': overlap_minutes
        }
    
    def _determine_conflict_severity(self, conflict_info: Dict, 
                                    event1: Dict, event2: Dict) -> str:
        """Determine the severity level of a conflict"""
        overlap_minutes = conflict_info['overlap_minutes']
        conflict_type = conflict_info['conflict_type']
        
        # High severity criteria
        if conflict_type == 'exact_overlap':
            return 'high'
        if overlap_minutes >= 60:  # More than 1 hour overlap
            return 'high'
        if conflict_type == 'time_overlap' and overlap_minutes >= 30:
            return 'high'
            
        # Low severity criteria
        if conflict_type == 'all_day_overlap':
            return 'low'
        if overlap_minutes <= 15:  # 15 minutes or less
            return 'low'
        if 'tentative' in str(event1.get('status', '')).lower() or \
           'tentative' in str(event2.get('status', '')).lower():
            return 'low'
            
        # Default to medium
        return 'medium'
    
    def _meets_severity_threshold(self, severity: str, threshold: str) -> bool:
        """Check if a conflict severity meets the threshold"""
        severity_levels = {'low': 1, 'medium': 2, 'high': 3}
        
        if threshold == 'all':
            return True
        
        threshold_level = severity_levels.get(threshold, 0)
        conflict_level = severity_levels.get(severity, 0)
        
        return conflict_level >= threshold_level
    
    def _generate_conflict_recommendations(self, conflicts_by_severity: Dict) -> List[str]:
        """Generate recommendations based on conflict analysis"""
        recommendations = []
        
        high_count = len(conflicts_by_severity['high'])
        medium_count = len(conflicts_by_severity['medium'])
        low_count = len(conflicts_by_severity['low'])
        
        if high_count > 0:
            recommendations.append(
                f"⚠️ You have {high_count} high-severity conflicts that need immediate attention"
            )
            
        if high_count > 3:
            recommendations.append(
                "Consider rescheduling some meetings or delegating responsibilities"
            )
            
        if medium_count > 5:
            recommendations.append(
                "Review medium-severity conflicts to see if any can be adjusted"
            )
            
        if low_count > 10:
            recommendations.append(
                "Many low-severity conflicts detected - your calendar might be over-scheduled"
            )
            
        if high_count == 0 and medium_count == 0:
            if low_count > 0:
                recommendations.append(
                    "✅ Only low-severity conflicts found - your schedule looks manageable"
                )
            else:
                recommendations.append(
                    "✅ No conflicts detected - your calendar is clear!"
                )
        
        return recommendations
    
    def _is_all_day_event(self, event: Dict[str, Any]) -> bool:
        """Check if an event is an all-day event"""
        # Check if the event has no time component (just date)
        start = event.get('start', '')
        end = event.get('end', '')
        
        # All-day events typically have dates without times
        # or have the same date with 00:00:00 times
        if 'T' not in str(start) or (
            'T00:00:00' in str(start) and 'T00:00:00' in str(end)
        ):
            return True
        
        # Check if duration is exactly 24 hours or multiples
        try:
            start_dt = self._normalize_datetime(start)
            end_dt = self._normalize_datetime(end)
            if start_dt and end_dt:
                duration = end_dt - start_dt
                # Check if duration is exactly 1 or more days
                if duration.total_seconds() % 86400 == 0 and duration.total_seconds() >= 86400:
                    # And starts at midnight
                    if start_dt.hour == 0 and start_dt.minute == 0:
                        return True
        except:
            pass
            
        return False
