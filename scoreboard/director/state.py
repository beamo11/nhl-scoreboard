"""Application state derived from the snapshot.

Sport sources publish a normalised ``main_event`` dict with a ``phase`` field so
the director stays sport-agnostic::

    {"phase": "pregame"|"live"|"intermission"|"postgame", "start_time_utc": ..., ...}

A ``pregame`` phase is split in two by the clock: GAMEDAY from midnight until
``sports.pregame_hours`` before the start, PREGAME from there to the first drop.
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from ..data import Snapshot
from ..isotime import parse_iso

MAIN_EVENT_KEY = "main_event"
SYSTEM_KEY = "system"


class AppState(str, Enum):
    BOOT = "boot"
    ERROR = "error"
    OFFSEASON = "offseason"
    OFFDAY = "offday"
    GAMEDAY = "gameday"
    PREGAME = "pregame"
    LIVE = "live"
    INTERMISSION = "intermission"
    POSTGAME = "postgame"


PLAYLIST_STATES = (AppState.OFFSEASON, AppState.OFFDAY, AppState.GAMEDAY, AppState.PREGAME, AppState.LIVE,
                   AppState.INTERMISSION, AppState.POSTGAME)
_PHASES = {s.value: s for s in (AppState.PREGAME, AppState.LIVE, AppState.INTERMISSION, AppState.POSTGAME)}
DEFAULT_PREGAME_HOURS = 1.0


def is_offline(snapshot: Snapshot) -> bool:
    return (snapshot.get(SYSTEM_KEY) or {}).get("online") is False


def has_data(snapshot: Snapshot) -> bool:
    """Anything besides the system key has ever been published."""
    return any(k != SYSTEM_KEY for k in snapshot.data)


def compute_state(snapshot: Snapshot, now: datetime | None = None, pregame_hours: float = DEFAULT_PREGAME_HOURS) -> AppState:
    """The state for ``snapshot`` at ``now`` (aware; defaults to the clock).

    ``pregame_hours`` is how long before the start the PREGAME playlist takes over from
    GAMEDAY. A game whose start time is missing or unreadable is PREGAME all day, as it
    was before the split: better the matchup board than a day of standings.
    """
    if is_offline(snapshot) and not has_data(snapshot):
        return AppState.ERROR                      # nothing to show: clock until we get data
    event = snapshot.get(MAIN_EVENT_KEY)
    if not event:
        return AppState.OFFSEASON if is_offseason(snapshot) else AppState.OFFDAY
    state = _PHASES.get(event.get("phase"), AppState.OFFDAY)
    if state == AppState.PREGAME and not pregame_window_open(event, now, pregame_hours):
        return AppState.GAMEDAY
    return state


def pregame_window_open(event: dict, now: datetime | None = None, pregame_hours: float = DEFAULT_PREGAME_HOURS) -> bool:
    """True once the start is ``pregame_hours`` away or less (or unknown)."""
    start = parse_iso(str(event.get("start_time_utc") or ""))
    if start is None:
        return True
    now = now or datetime.now(UTC)
    return (start - now).total_seconds() <= pregame_hours * 3600


def is_offseason(snapshot: Snapshot) -> bool:
    """Every sport that has reported a season phase says off-season (and at least one has)."""
    phases = [v.get("phase") for k, v in snapshot.data.items() if k.endswith(".season") and isinstance(v, dict)]
    return bool(phases) and all(p == "offseason" for p in phases)
