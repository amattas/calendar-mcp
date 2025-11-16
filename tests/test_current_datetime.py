"""Tests for get_current_datetime tool"""

import os
from datetime import datetime
from unittest.mock import patch


def test_get_current_datetime_utc():
    """Test get_current_datetime with default UTC timezone"""
    with patch.dict(os.environ, {"TIMEZONE": "UTC"}):
        # Import the function
        from src.server import get_current_datetime

        result = get_current_datetime()

        # Verify all expected fields are present
        assert "date" in result
        assert "time" in result
        assert "datetime" in result
        assert "timezone" in result
        assert "utc_offset" in result
        assert "timezone_abbr" in result
        assert "day_of_week" in result
        assert "timestamp" in result

        # Verify timezone
        assert result["timezone"] == "UTC"
        assert result["utc_offset"] == "+0000"

        # Verify date format (YYYY-MM-DD)
        assert len(result["date"]) == 10
        assert result["date"].count("-") == 2

        # Verify time format (HH:MM:SS)
        assert len(result["time"]) == 8
        assert result["time"].count(":") == 2

        # Verify ISO datetime format
        assert "T" in result["datetime"]

        # Verify day of week is a valid day name
        valid_days = [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]
        assert result["day_of_week"] in valid_days

        # Verify timestamp is a reasonable integer
        assert isinstance(result["timestamp"], int)
        assert result["timestamp"] > 1700000000  # After Nov 2023


def test_get_current_datetime_custom_timezone():
    """Test get_current_datetime with custom timezone"""
    with patch.dict(os.environ, {"TIMEZONE": "America/New_York"}):
        from src.server import get_current_datetime

        result = get_current_datetime()

        # Verify timezone
        assert result["timezone"] == "America/New_York"

        # Verify offset (should be -0400 or -0500 depending on DST)
        assert result["utc_offset"] in ["-0400", "-0500"]
        assert result["timezone_abbr"] in ["EDT", "EST"]


def test_get_current_datetime_asia_timezone():
    """Test get_current_datetime with Asian timezone"""
    with patch.dict(os.environ, {"TIMEZONE": "Asia/Tokyo"}):
        from src.server import get_current_datetime

        result = get_current_datetime()

        # Verify timezone
        assert result["timezone"] == "Asia/Tokyo"

        # Verify positive offset for Tokyo
        assert result["utc_offset"] == "+0900"
        assert result["timezone_abbr"] == "JST"


def test_get_current_datetime_invalid_timezone_fallback():
    """Test that invalid timezone falls back to UTC"""
    with patch.dict(os.environ, {"TIMEZONE": "Invalid/Timezone"}):
        from src.server import get_current_datetime

        result = get_current_datetime()

        # Should fall back to UTC
        assert result["timezone"] == "UTC"
        assert result["utc_offset"] == "+0000"


def test_get_current_datetime_no_timezone_env():
    """Test get_current_datetime when TIMEZONE env var is not set"""
    with patch.dict(os.environ, {}, clear=True):
        from src.server import get_current_datetime

        result = get_current_datetime()

        # Should default to UTC
        assert result["timezone"] == "UTC"
        assert result["utc_offset"] == "+0000"


def test_get_current_datetime_europe_timezone():
    """Test get_current_datetime with European timezone"""
    with patch.dict(os.environ, {"TIMEZONE": "Europe/London"}):
        from src.server import get_current_datetime

        result = get_current_datetime()

        # Verify timezone
        assert result["timezone"] == "Europe/London"

        # Verify offset (should be +0000 or +0100 depending on DST)
        assert result["utc_offset"] in ["+0000", "+0100"]
        assert result["timezone_abbr"] in ["GMT", "BST"]


def test_datetime_fields_are_synchronized():
    """Test that all datetime fields represent the same moment in time"""
    with patch.dict(os.environ, {"TIMEZONE": "America/Los_Angeles"}):
        from src.server import get_current_datetime

        result = get_current_datetime()

        # Parse the ISO datetime
        dt_from_iso = datetime.fromisoformat(result["datetime"])

        # Verify the date matches
        assert dt_from_iso.strftime("%Y-%m-%d") == result["date"]

        # Verify the time matches (within same second)
        assert dt_from_iso.strftime("%H:%M:%S") == result["time"]

        # Verify timestamp represents the same time
        assert int(dt_from_iso.timestamp()) == result["timestamp"]
