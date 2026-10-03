"""Simulation clock engine managing pure discrete virtual simulation time (America/New_York ET)."""

from datetime import datetime, timedelta
from typing import Callable, List, Optional
import logging

from simtrade.utils import to_eastern_time, NY_TZ

logger = logging.getLogger(__name__)


class SimClock:
    """Manages discrete virtual simulation time in Eastern Time without wall-clock dependencies."""

    def __init__(
        self,
        start_time: Optional[datetime] = None,
        speed_multiplier: float = 1.0,
        interval_seconds: int = 60,
        timeline: Optional[List[datetime]] = None,
    ):
        self.sim_timeline: List[datetime] = [to_eastern_time(t) for t in (timeline or [])]
        self.cursor = 0

        if self.sim_timeline:
            self.sim_start_time: datetime = self.sim_timeline[0]
            self.cursor = 1
        elif start_time:
            self.sim_start_time: datetime = to_eastern_time(start_time)
        else:
            self.sim_start_time: datetime = datetime(2026, 1, 5, 9, 30, 0, tzinfo=NY_TZ)

        self.sim_current_time: datetime = self.sim_start_time
        self.sim_interval: timedelta = timedelta(seconds=interval_seconds)
        self.speed_multiplier: float = speed_multiplier
        self.is_running: bool = False
        self.is_paused: bool = False
        self.step_count: int = 0
        self._listeners: List[Callable[[datetime], None]] = []

    @property
    def current_time(self) -> datetime:
        return self.sim_current_time

    @current_time.setter
    def current_time(self, val: datetime):
        self.sim_current_time = to_eastern_time(val)

    @property
    def timeline(self) -> List[datetime]:
        return self.sim_timeline

    @timeline.setter
    def timeline(self, val: List[datetime]):
        self.sim_timeline = [to_eastern_time(t) for t in val]

    @property
    def interval(self) -> timedelta:
        return self.sim_interval

    @interval.setter
    def interval(self, val: timedelta):
        self.sim_interval = val

    def now(self) -> datetime:
        """
        Return the current virtual simulation time in America/New_York (ET).
        Independent of local machine wall-clock time.
        """
        return self.sim_current_time

    def set_time(self, new_sim_time: datetime, reason: str = "SET_TIME"):
        """Explicitly set the current virtual simulation time."""
        self.sim_current_time = to_eastern_time(new_sim_time)
        logger.debug(f"[CLOCK] Time set to {self.sim_current_time.isoformat()} ({reason})")

    def step_to(self, target_time: datetime) -> datetime:
        """Advance virtual simulation time directly to target_time."""
        self.sim_current_time = to_eastern_time(target_time)
        self.step_count += 1
        for listener in self._listeners:
            try:
                listener(self.sim_current_time)
            except Exception as e:
                logger.error(f"Error in clock listener: {e}")
        return self.sim_current_time

    def step(self) -> datetime:
        """Advance the simulation time by one interval or the next timeline bar."""
        if self.sim_timeline and self.cursor < len(self.sim_timeline):
            self.sim_current_time = self.sim_timeline[self.cursor]
            self.cursor += 1
        else:
            self.sim_current_time += self.sim_interval
        self.step_count += 1

        for listener in self._listeners:
            try:
                listener(self.sim_current_time)
            except Exception as e:
                logger.error(f"Error in clock listener: {e}")
        return self.sim_current_time

    def add_listener(self, callback: Callable[[datetime], None]):
        self._listeners.append(callback)

    def pause(self):
        self.is_paused = True

    def resume(self):
        self.is_paused = False

    def set_speed(self, speed: float):
        self.speed_multiplier = float(speed)

    async def sleep_for_speed(self):
        """No-op yield in discrete mode."""
        import asyncio
        await asyncio.sleep(0.0001)
