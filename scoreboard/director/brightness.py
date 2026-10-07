"""Brightness from config + time of day (fixed / sunrise-sunset / fixed hours)."""
from __future__ import annotations
from datetime import datetime, time, timedelta
from astral import LocationInfo
from astral.sun import sun
from ..config.models import BrightnessConfig, LocationConfig

def _parse_hhmm(value: str) -> time | None:
    """None for a value that is not a clock time.
    ``24:00`` is accepted as midnight for backwards compatibility with
    configuration values that use it to mean the end of a day.
    """
    try:
        hours, minutes = (int(part) for part in value.split(":"))
        return time(0, 0) if (hours, minutes) == (24, 0) else time(hours, minutes)
    except (ValueError, TypeError):
        return None

def _in_window(now: time, start: time, end: time) -> bool:
    """Return whether ``now`` falls within a time window.
    Windows that cross midnight are supported. The end time is exclusive.
    """
    if start <= end:
        return start <= now < end
    return now >= start or now < end

def _in_configured_window(
    now: time,
    start_value: str,
    end_value: str,
) -> bool:
    """Return whether ``now`` falls within a configured HH:MM window."""
    start = _parse_hhmm(start_value)
    end = _parse_hhmm(end_value)
    return (
        start is not None
        and end is not None
        and _in_window(now, start, end)
    )

def is_night(
    now: datetime,
    cfg: BrightnessConfig,
    loc: LocationConfig,
) -> bool:
    if cfg.mode == "fixed":
        return False

    if cfg.mode == "hours" or loc.latitude is None or loc.longitude is None:
        return _in_configured_window(
            now.time(),
            cfg.night_start,
            cfg.night_end,
        )
    info = LocationInfo(
        latitude=loc.latitude,
        longitude=loc.longitude,
        timezone=loc.timezone,
    )
    try:
        s = sun(
            info.observer,
            date=now.date(),
            tzinfo=now.tzinfo,
        )
    except ValueError:  # polar day/night
        return False
    offset = timedelta(minutes=cfg.sunset_offset_minutes)
    return (
        now >= s["sunset"] + offset
        or now < s["sunrise"] - offset
    )

def brightness_for(
    now: datetime,
    cfg: BrightnessConfig,
    loc: LocationConfig,
    live: bool,
) -> int:
    if live and cfg.keep_bright_when_live:
        return cfg.day
    return cfg.night if is_night(now, cfg, loc) else cfg.day

def sleep_mode_active(
    now: datetime,
    cfg: BrightnessConfig,
) -> bool:
    """Return whether sleep mode should currently turn the display off."""
    if not cfg.sleep_mode:
        return False

    return _in_configured_window(
        now.time(),
        cfg.sleep_start,
        cfg.sleep_end,
    )
