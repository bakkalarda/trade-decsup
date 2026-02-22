"""Event calendar — detect proximity to high-impact macro events.

Used by the veto engine to block new entries within a configurable
window around FOMC, CPI, NFP, and other tier-1 macro releases.

For backtesting: generates approximate event dates from recurring patterns.
For live: could be extended with FRED/calendar API integration.
"""

from __future__ import annotations

import calendar
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "events.yaml"


class EventCalendar:
    """Simple macro event calendar for veto decisions."""

    def __init__(self, config_path: Optional[Path] = None):
        path = config_path or _DEFAULT_CONFIG_PATH
        self._config = {}
        self._events_cache: dict[str, list[datetime]] = {}

        if path.exists():
            with open(path) as f:
                self._config = yaml.safe_load(f) or {}
        else:
            logger.warning("Event calendar config not found: %s", path)

        self.veto_hours_before = self._config.get("event_veto_hours_before", 6)
        self.veto_hours_after = self._config.get("event_veto_hours_after", 2)

    def is_in_event_window(self, bar_time: datetime) -> tuple[bool, Optional[str]]:
        """Check if bar_time falls within a veto window around any event.

        Returns:
            (is_vetoed: bool, event_name: str | None)
        """
        year = bar_time.year
        events = self._get_events_for_year(year)

        for event_time, event_name in events:
            window_start = event_time - timedelta(hours=self.veto_hours_before)
            window_end = event_time + timedelta(hours=self.veto_hours_after)

            if window_start <= bar_time <= window_end:
                return True, event_name

        return False, None

    def _get_events_for_year(self, year: int) -> list[tuple[datetime, str]]:
        """Generate event dates for a given year from recurring patterns."""
        cache_key = str(year)
        if cache_key in self._events_cache:
            return [(dt, name) for dt, name in zip(
                self._events_cache[cache_key],
                self._events_cache.get(f"{cache_key}_names", [])
            )]

        events: list[tuple[datetime, str]] = []
        recurring = self._config.get("recurring_events", [])

        for event_def in recurring:
            name = event_def.get("name", "Unknown")
            months = event_def.get("months", [])
            weekday = event_def.get("weekday", 2)  # Wednesday default
            week_of_month = event_def.get("week_of_month", 1)
            hour_utc = event_def.get("hour_utc", 18)

            for month in months:
                dt = self._find_nth_weekday(year, month, weekday, week_of_month, hour_utc)
                if dt:
                    events.append((dt, name))

        events.sort(key=lambda x: x[0])

        # Cache
        self._events_cache[cache_key] = [e[0] for e in events]
        self._events_cache[f"{cache_key}_names"] = [e[1] for e in events]

        return events

    @staticmethod
    def _find_nth_weekday(
        year: int, month: int, weekday: int, n: int, hour: int
    ) -> Optional[datetime]:
        """Find the nth occurrence of a weekday in a given month.

        weekday: 0=Monday, 6=Sunday
        n: 1-based (1st, 2nd, 3rd, ...)
        """
        try:
            cal = calendar.Calendar(firstweekday=0)
            month_days = cal.itermonthdays2(year, month)
            count = 0
            for day, wd in month_days:
                if day == 0:
                    continue
                if wd == weekday:
                    count += 1
                    if count == n:
                        return datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc)
            return None
        except Exception:
            return None
