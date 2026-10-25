"""When do scheduled scans run? Pure logic - the scan manager calls `due()` every few seconds.

Per profile (settings):
  scheduleMode             "off" | "interval" | "daily"
  scheduleIntervalMinutes  interval mode: minutes between scan starts (anchored on the last start,
                           so a restart of the app does not shift or skip the rhythm; a scan that
                           was missed while the device was off runs once, shortly after the start)
  scheduleTimes            daily mode: "02:00, 14:30" in the device's local time
  scheduleDays             daily mode: "mon,tue,wed,thu,fri" (empty = every day)
Daily slots that passed while the app was not running are not caught up.
The device clock matters for daily mode: check Settings -> Date & Time on the device (NTP).
"""

import re
from datetime import datetime, timedelta, timezone

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
MODES = ("off", "interval", "daily")
STARTUP_DELAY = timedelta(seconds=60)   # let the device finish booting before the first scan
_SPLIT = re.compile(r"[\s,;]+")


def parse_times(text):
    """"02:00, 14:30" -> [(2, 0), (14, 30)]. Raises ValueError."""
    result = []
    for item in [s for s in _SPLIT.split((text or "").strip()) if s]:
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", item)
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise ValueError("{!r}: expected HH:MM (00:00 .. 23:59)".format(item))
        result.append((int(m.group(1)), int(m.group(2))))
    return sorted(set(result))


def parse_days(text):
    """"mon,wed" -> {0, 2}; empty = every day. Raises ValueError."""
    items = [s.lower()[:3] for s in _SPLIT.split((text or "").strip()) if s]
    if not items:
        return set(range(7))
    days = set()
    for item in items:
        if item not in WEEKDAYS:
            raise ValueError("{!r}: expected mon, tue, wed, thu, fri, sat or sun".format(item))
        days.add(WEEKDAYS.index(item))
    return days


def check_profile(profile):
    """Error messages for the schedule fields of a profile."""
    errors = []
    mode = profile.get("scheduleMode", "off")
    if mode == "daily":
        try:
            if not parse_times(profile.get("scheduleTimes", "")):
                errors.append("scheduleTimes: at least one time (HH:MM) for a daily schedule")
        except ValueError as exc:
            errors.append("scheduleTimes: {}".format(exc))
    try:
        parse_days(profile.get("scheduleDays", ""))
    except ValueError as exc:
        errors.append("scheduleDays: {}".format(exc))
    return errors


def parse_iso(text):
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _local_slot(day, hour, minute, tz):
    naive = datetime(day.year, day.month, day.day, hour, minute)
    return naive.replace(tzinfo=tz) if tz is not None else naive.astimezone()


def next_run(profile, last_started, now, app_started, tz=None):
    """Next start time (aware datetime) of a profile's schedule, or None (off/invalid).

    last_started: aware datetime of the last start (scan or schedule fire) or None
    now, app_started: aware datetimes; tz: fixed tzinfo for tests (None = the device's local time)
    """
    mode = profile.get("scheduleMode", "off")
    if mode == "interval":
        interval = timedelta(minutes=max(1, int(profile.get("scheduleIntervalMinutes", 60))))
        earliest = app_started + STARTUP_DELAY
        if last_started is None or last_started > now:   # never ran, or the clock went back
            return max(earliest, now)
        return max(last_started + interval, earliest)
    if mode == "daily":
        try:
            times = parse_times(profile.get("scheduleTimes", ""))
            days = parse_days(profile.get("scheduleDays", ""))
        except ValueError:
            return None
        if not times:
            return None
        reference = app_started
        if last_started is not None and last_started <= now:
            reference = max(reference, last_started)
        local_now = now.astimezone(tz) if tz is not None else now.astimezone()
        for offset in range(-1, 9):
            day = (local_now + timedelta(days=offset)).date()
            if day.weekday() not in days:
                continue
            for hour, minute in times:
                slot = _local_slot(day, hour, minute, tz)
                if slot > reference:
                    return slot
        return None
    return None


class Scheduler:
    """Remembers what it fired, so a scan that is still running is not started again."""

    def __init__(self, clock=None, tz=None):
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._tz = tz
        self.app_started = self._clock()
        self._fired = {}   # profile id -> aware datetime

    def _last(self, profile_id, last_started_iso):
        values = [v for v in (parse_iso(last_started_iso), self._fired.get(profile_id)) if v is not None]
        return max(values) if values else None

    def next_runs(self, profiles, last_started):
        """{profile id: next start ISO (UTC)} for enabled schedules. last_started: fn(id) -> ISO|None"""
        now = self._clock()
        result = {}
        for p in profiles:
            when = next_run(p, self._last(p["id"], last_started(p["id"])), now, self.app_started, self._tz)
            if when is not None:
                result[p["id"]] = when.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        return result

    def due(self, profiles, last_started, busy):
        """Profile ids whose scheduled start has come (and marks them fired). busy: set of profile
        ids running or queued - they are not fired again."""
        now = self._clock()
        due = []
        for p in profiles:
            if p["id"] in busy:
                continue
            when = next_run(p, self._last(p["id"], last_started(p["id"])), now, self.app_started, self._tz)
            if when is not None and when <= now:
                self._fired[p["id"]] = now
                due.append(p["id"])
        return due
