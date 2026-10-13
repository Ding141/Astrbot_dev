from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from datetime import time as dt_time

from dateutil.rrule import rrulestr
from icalendar import Calendar

from astrbot.api import logger

from .course_types import CourseEvent

SHANGHAI_TZ = timezone(timedelta(hours=8))


class IcsParser:
    def __init__(self):
        self._cache: dict[str, list[CourseEvent]] = {}

    def clear_cache(self, ics_path: str) -> None:
        self._cache.pop(ics_path, None)

    def parse_ics_file(self, file_path: str) -> list[CourseEvent]:
        """Parse course sessions from the current Shanghai week onward.

        Args:
            file_path: Path to the calendar file.

        Returns:
            Course sessions sorted by start time, or an empty list on failure.
        """
        if file_path in self._cache:
            return self._cache[file_path]

        try:
            cal_content = open(file_path, encoding="utf-8").read()
        except Exception as e:
            logger.error(f"[course] cannot read ics: {e}")
            return []

        try:
            cal = Calendar.from_ical(cal_content)
        except Exception as e:
            logger.error(f"[course] invalid ics format: {e}")
            return []
        today = datetime.now(SHANGHAI_TZ).date()
        first_day_of_week = today - timedelta(days=today.weekday())

        events: list[CourseEvent] = []

        for component in cal.walk():
            if component.name != "VEVENT":
                continue

            try:
                summary = str(component.get("summary") or "")
                description = str(component.get("description") or "")
                location = str(component.get("location") or "")
                dtstart_obj = component.get("dtstart")
                dtend_obj = component.get("dtend")
                if not dtstart_obj or not dtend_obj:
                    logger.warning(
                        "[course] skip invalid vevent: missing dtstart/dtend"
                    )
                    continue
                dtstart = dtstart_obj.dt
                dtend = dtend_obj.dt
                rrule_str = component.get("rrule")

                if isinstance(dtstart, date) and not isinstance(dtstart, datetime):
                    dtstart = datetime.combine(dtstart, dt_time.min)
                if isinstance(dtend, date) and not isinstance(dtend, datetime):
                    dtend = datetime.combine(dtend, dt_time.min)

                dtstart = (
                    dtstart.astimezone(SHANGHAI_TZ)
                    if getattr(dtstart, "tzinfo", None)
                    else dtstart.replace(tzinfo=SHANGHAI_TZ)
                )
                dtend = (
                    dtend.astimezone(SHANGHAI_TZ)
                    if getattr(dtend, "tzinfo", None)
                    else dtend.replace(tzinfo=SHANGHAI_TZ)
                )

                duration = dtend - dtstart

                if rrule_str:
                    if "UNTIL" in rrule_str:
                        until_dt = rrule_str["UNTIL"][0]
                        if isinstance(until_dt, date) and not isinstance(
                            until_dt, datetime
                        ):
                            until_dt = datetime.combine(until_dt, dt_time.max)
                        if until_dt.tzinfo is None:
                            until_dt = until_dt.replace(tzinfo=SHANGHAI_TZ)
                        rrule_str["UNTIL"][0] = until_dt.astimezone(timezone.utc)

                    dtstart_utc = dtstart.astimezone(timezone.utc)
                    rule = rrulestr(rrule_str.to_ical().decode(), dtstart=dtstart_utc)

                    start_of_week_utc = datetime.combine(
                        first_day_of_week, dt_time.min, tzinfo=SHANGHAI_TZ
                    ).astimezone(timezone.utc)
                    future_limit_utc = start_of_week_utc + timedelta(days=365)

                    for occ_utc in rule.between(
                        start_of_week_utc, future_limit_utc, inc=True
                    ):
                        occ_local = occ_utc.astimezone(SHANGHAI_TZ)
                        events.append(
                            CourseEvent(
                                summary=summary,
                                description=description,
                                location=location,
                                start_time=occ_local,
                                end_time=occ_local + duration,
                            )
                        )

                else:
                    if dtstart.date() >= first_day_of_week:
                        events.append(
                            CourseEvent(
                                summary=summary,
                                description=description,
                                location=location,
                                start_time=dtstart,
                                end_time=dtend,
                            )
                        )
            except Exception as e:
                logger.warning(f"[course] skip invalid vevent: {e}")
                continue

        events.sort(key=lambda e: e.start_time)
        self._cache[file_path] = events
        return events
