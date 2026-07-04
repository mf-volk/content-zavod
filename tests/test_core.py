"""Tests for scheduling and timezone handling."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import pytz


def test_parse_count_basic():
    """Test parsing basic numbers."""
    from app.donor_parser import parse_count

    assert parse_count("123") == 123
    assert parse_count("1000") == 1000
    assert parse_count("") == 0
    assert parse_count("abc") == 0


def test_parse_count_k_format():
    """Test parsing K format (thousands)."""
    from app.donor_parser import parse_count

    assert parse_count("1.2K") == 1200
    assert parse_count("3.5K") == 3500
    assert parse_count("10K") == 10000


def test_parse_count_m_format():
    """Test parsing M format (millions)."""
    from app.donor_parser import parse_count

    assert parse_count("1.2M") == 1200000
    assert parse_count("2.5M") == 2500000


def test_timezone_conversion():
    """Test timezone conversion for scheduling."""
    # Moscow timezone
    msk = pytz.timezone("Europe/Moscow")

    # Create a naive datetime
    naive_dt = datetime(2024, 12, 15, 18, 0)  # 18:00

    # Localize to MSK
    msk_dt = msk.localize(naive_dt)

    # Convert to UTC
    utc_dt = msk_dt.astimezone(pytz.UTC)

    # MSK is UTC+3, so 18:00 MSK = 15:00 UTC
    assert utc_dt.hour == 15


def test_schedule_in_future():
    """Test that scheduled time is in the future."""
    msk = pytz.timezone("Europe/Moscow")

    # Get current time
    now = datetime.now(msk)

    # Schedule 1 hour later
    scheduled = now + timedelta(hours=1)

    # Verify it's in the future
    assert scheduled > now


def test_extract_title():
    """Test title extraction from text."""
    from app.donor_parser import extract_title

    # Short text
    short_text = "Hello world"
    assert extract_title(short_text) == "Hello world"

    # Long text
    long_text = "A" * 150
    title = extract_title(long_text, max_length=100)
    assert len(title) <= 103  # 100 + "..."
    assert title.endswith("...")


def test_extract_text_html_cleaning():
    """Test HTML cleaning in text extraction."""
    from app.donor_parser import extract_text

    html = '<div class="tgme_widget_message_text">Hello<br/>World&nbsp;Test</div>'
    result = extract_text(html)

    assert "Hello" in result
    assert "World" in result
    assert "Test" in result
    assert "<br" not in result
    assert "&nbsp;" not in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
