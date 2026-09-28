"""Utility functions for timezone-aware datetimes and formatting."""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)
