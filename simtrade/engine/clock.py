"""Simulation clock engine managing virtual time, pace, and step progression."""

import asyncio
from datetime import datetime, timedelta
from typing import Callable, List, Optional
import logging

logger = logging.getLogger(__name__)


class SimClock:
    """Manages virtual simulation time and playback pacing."""

    def __init__(
        self,
        start_time: Optional[datetime] = None,
        speed_multiplier: float = 1.0,
        interval_seconds: int = 60,
        timeline: Optional[List[datetime]] = None,
    ):
        self.timeline = timeline or []
        self.cursor = 0
        if self.timeline:
            self.current_time = self.timeline[0]
            self.cursor = 1
        else:
            self.current_time: datetime = start_time or datetime(2026, 1, 5, 9, 30, 0)
        self.interval = timedelta(seconds=interval_seconds)
        self.speed_multiplier = speed_multiplier  # 1.0 = real-time, 60.0 = 1 sec per min, 0 = instant
        self.is_running: bool = False
        self.is_paused: bool = True
        self.step_count: int = 0
        self._listeners: List[Callable[[datetime], None]] = []

    def set_time(self, new_time: datetime):
        self.current_time = new_time

    def set_speed(self, speed: float):
        """Set simulation speed. 1.0 = 60s wall clock per simulated min. 60.0 = 1s per min."""
        self.speed_multiplier = max(0.0, float(speed))
        logger.info(f"Simulation speed updated to {self.speed_multiplier}x")

    def pause(self):
        self.is_paused = True
        logger.info("Simulation clock paused")

    def resume(self):
        self.is_paused = False
        logger.info("Simulation clock resumed")

    def step(self) -> datetime:
        """Advance the simulation time by one interval or the next timeline bar."""
        if self.timeline and self.cursor < len(self.timeline):
            self.current_time = self.timeline[self.cursor]
            self.cursor += 1
        else:
            self.current_time += self.interval
        self.step_count += 1
        for listener in self._listeners:
            try:
                listener(self.current_time)
            except Exception as e:
                logger.error(f"Error in clock listener: {e}")
        return self.current_time

    def add_listener(self, callback: Callable[[datetime], None]):
        self._listeners.append(callback)

    async def sleep_for_speed(self):
        """Sleep proportionally to virtual interval based on speed_multiplier."""
        if self.speed_multiplier <= 0:
            # Max speed: yield control briefly to event loop so tasks/websockets can process
            await asyncio.sleep(0.001)
            return

        # Virtual interval is e.g. 60 seconds.
        # If speed = 1.0, wait 60s.
        # If speed = 60.0, wait 1.0s.
        # If speed = 120.0, wait 0.5s.
        wall_wait = self.interval.total_seconds() / self.speed_multiplier
        # Clamp to at least 1ms to allow IO
        wall_wait = max(0.001, wall_wait)
        await asyncio.sleep(wall_wait)
