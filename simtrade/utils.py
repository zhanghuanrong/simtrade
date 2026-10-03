"""Utility functions for timezone-aware datetimes, formatting, and market hours."""

from datetime import datetime, time, timezone
from typing import Tuple
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")
MARKET_OPEN_TIME = time(9, 30, 0)
MARKET_CLOSE_TIME = time(16, 0, 0)


def utc_now() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


from typing import Tuple, Union


def to_eastern_time(dt: Union[datetime, str]) -> datetime:
    """Convert any datetime or ISO string (UTC aware, naive, or local) to America/New_York Eastern Time."""
    if isinstance(dt, str):
        # Handle ISO strings
        dt_str = dt.replace("Z", "+00:00")
        dt = datetime.fromisoformat(dt_str)
    if getattr(dt, "tzinfo", None) is not None:
        return dt.astimezone(NY_TZ)
    # Naive timestamp handling: treat as America/New_York
    return dt.replace(tzinfo=NY_TZ)


def is_regular_trading_hours(dt: datetime) -> Tuple[bool, str]:
    """
    Check if a given datetime falls within Regular Trading Hours (RTH).
    
    Regular Trading Hours:
    - Monday through Friday
    - 09:30:00 to 16:00:00 US Eastern Time (America/New_York)
    
    Returns (is_rth, reason_str).
    """
    dt_et = to_eastern_time(dt)
    weekday = dt_et.weekday()
    if weekday >= 5:
        day_name = "Saturday" if weekday == 5 else "Sunday"
        return False, f"Market is closed on weekends ({day_name}). Regular trading hours are Mon-Fri 09:30-16:00 ET."

    t = dt_et.time()
    if t < MARKET_OPEN_TIME:
        return False, (
            f"Market is closed: Pre-market trading is disabled (order time: {t.strftime('%H:%M:%S')} ET). "
            f"Regular trading hours are 09:30-16:00 ET."
        )
    if t > MARKET_CLOSE_TIME:
        session = "Post-market" if t <= time(20, 0, 0) else "Overnight"
        return False, (
            f"Market is closed: {session} trading is disabled (order time: {t.strftime('%H:%M:%S')} ET). "
            f"Regular trading hours are 09:30-16:00 ET."
        )
    return True, "Regular Trading Hours"
