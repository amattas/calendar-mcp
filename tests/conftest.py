"""Shared fixtures and configuration for tests"""

import os
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime, timezone, timedelta


# ============================================================================
# iCalendar Fixtures
# ============================================================================


@pytest.fixture
def sample_ical_data():
    """Sample iCalendar data for testing"""
    return """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test Calendar//EN
BEGIN:VEVENT
UID:test-event-1@example.com
DTSTART:20240101T100000Z
DTEND:20240101T110000Z
SUMMARY:Test Event 1
DESCRIPTION:This is a test event
LOCATION:Conference Room
END:VEVENT
BEGIN:VEVENT
UID:test-event-2@example.com
DTSTART:20240102T140000Z
DTEND:20240102T150000Z
SUMMARY:Test Event 2
RRULE:FREQ=WEEKLY;COUNT=4
END:VEVENT
END:VCALENDAR"""


@pytest.fixture
def sample_ical_with_timezone():
    """Sample iCalendar data with timezone information"""
    return """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test Calendar//EN
BEGIN:VTIMEZONE
TZID:America/New_York
BEGIN:STANDARD
DTSTART:20231105T020000
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:20240310T020000
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
END:DAYLIGHT
END:VTIMEZONE
BEGIN:VEVENT
UID:tz-event-1@example.com
DTSTART;TZID=America/New_York:20240101T100000
DTEND;TZID=America/New_York:20240101T110000
SUMMARY:Event with Timezone
END:VEVENT
END:VCALENDAR"""


@pytest.fixture
def mock_ical_feeds():
    """Mock iCalendar feed URLs and responses"""
    with patch("requests.get") as mock_get:

        def side_effect(url, *args, **kwargs):
            response = MagicMock()
            response.status_code = 200

            if "personal" in url:
                response.text = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:personal-1@example.com
DTSTART:20240101T090000Z
DTEND:20240101T100000Z
SUMMARY:Personal Event
END:VEVENT
END:VCALENDAR"""
            elif "work" in url:
                response.text = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:work-1@example.com
DTSTART:20240101T090000Z
DTEND:20240101T100000Z
SUMMARY:Work Meeting
END:VEVENT
END:VCALENDAR"""
            else:
                response.text = """BEGIN:VCALENDAR
VERSION:2.0
END:VCALENDAR"""

            return response

        mock_get.side_effect = side_effect
        yield mock_get


# ============================================================================
# Server/MCP Fixtures
# ============================================================================


@pytest.fixture
def mock_fastmcp():
    """Mock FastMCP server"""
    mock_mcp = MagicMock()
    mock_mcp.tool = MagicMock(return_value=lambda func: func)
    mock_mcp.resource = MagicMock(return_value=lambda func: func)
    mock_mcp.prompt = MagicMock(return_value=lambda func: func)
    return mock_mcp


@pytest.fixture
def mock_env_vars():
    """Set up environment variables for testing"""
    env_vars = {
        "ICAL_PERSONAL_URL": "http://example.com/personal.ics",
        "ICAL_WORK_URL": "http://example.com/work.ics",
        "MCP_API_KEY": "test_mcp_key",
    }
    with patch.dict(os.environ, env_vars):
        yield env_vars


# ============================================================================
# Async Fixtures
# ============================================================================


@pytest.fixture
def async_mock():
    """Create an async mock function"""
    return AsyncMock()


# ============================================================================
# Test Data Fixtures
# ============================================================================


@pytest.fixture
def test_dates():
    """Common test dates"""
    now = datetime.now(timezone.utc)
    return {
        "today": now.date(),
        "tomorrow": (now + timedelta(days=1)).date(),
        "yesterday": (now - timedelta(days=1)).date(),
        "next_week": (now + timedelta(days=7)).date(),
        "last_week": (now - timedelta(days=7)).date(),
        "now": now,
        "one_hour_ago": now - timedelta(hours=1),
        "one_hour_later": now + timedelta(hours=1),
    }
