"""How many times each airframe has been seen.

The poll only ever says what is in range *now*; this remembers it. One flyover spans
several polls, so a visit is counted once: an airframe seen again within ``gap_seconds``
of its last poll is the same visit, later than that is a new one.

Keyed by ICAO hex (every aircraft has one, and it names the airframe); the registration
is what people recognise, so it is kept for display. Persisted as one JSON file under the
data directory, next to the uploaded holiday pictures, so an update cannot wipe it.
Writes are atomic and debounced, and only happen for something worth keeping: a new visit,
an airframe never seen before, a registration or type that just became known. A poll that
merely sees the same aircraft still overhead moves ``last_seen`` in memory and nothing else
(it is written on the next real change or at shutdown); rewriting a 700 KB file every minute
for as long as anything was in range came to a gigabyte a day on an SD card. A file that
will not parse is set aside, never trusted.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

VISIT_GAP_SECONDS = 30 * 60
MAX_AIRFRAMES = 5000
DAILY_KEEP = 60
VISIT_HISTORY_KEEP = 5000
VISIT_HISTORY_DAYS = 60
SAVE_INTERVAL_SECONDS = 15 * 60.0
TOP_N = 5
REGULAR_FIELDS = ("hex", "registration", "type", "operator", "count", "last_seen")


def _entry_from(ac: dict[str, Any], prev: dict[str, Any] | None, now: float, new_visit: bool) -> dict[str, Any]:
    prev = prev or {}
    entry = {
        "registration": ac.get("registration") or prev.get("registration", ""),
        "type": ac.get("type") or prev.get("type", ""),
        "operator": ac.get("operator") or ac.get("airline") or prev.get("operator", ""),
        "count": int(prev.get("count", 0)) + (1 if new_visit else 0),
        "first_seen": prev.get("first_seen", now),
        "visit_started_at": now if new_visit else prev.get("visit_started_at", prev.get("first_seen", now)),
        "last_seen": now,
        **({"callsign": ac.get("callsign") or prev.get("callsign", "")} if ac.get("callsign") or prev.get("callsign") else {}),
        **({"operator_lookup_at": prev["operator_lookup_at"]} if prev.get("operator_lookup_at") is not None else {}),
        **({"operator_lookup_day": prev["operator_lookup_day"]} if prev.get("operator_lookup_day") else {}),
    }
    if prev.get("max_altitude_ft") is not None:
        entry["max_altitude_ft"] = prev["max_altitude_ft"]
        entry["max_altitude_at"] = prev.get("max_altitude_at")
    altitude = ac.get("altitude_ft")
    if isinstance(altitude, (int, float)) and altitude >= 0 and altitude > entry.get("max_altitude_ft", -1):
        entry["max_altitude_ft"] = int(altitude)
        entry["max_altitude_at"] = now
    return entry


def _valid_entry(e: Any) -> bool:
    return isinstance(e, dict) and isinstance(e.get("count"), int) and isinstance(e.get("last_seen"), (int, float))


def parse(content: bytes) -> tuple[dict[str, dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
    """``(airframes, daily, visits)`` from a log file's bytes, keeping well-formed entries.
    Raises ``ValueError`` for anything that is not a log at all."""
    try:
        doc = json.loads(content)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    airframes = doc.get("airframes") if isinstance(doc, dict) else None
    daily = doc.get("daily") if isinstance(doc, dict) else None
    visits = doc.get("visits", []) if isinstance(doc, dict) else None
    if not isinstance(airframes, dict) or not isinstance(daily, dict) or not isinstance(visits, list):
        raise ValueError("unexpected shape")
    try:
        return ({h: e for h, e in airframes.items() if isinstance(h, str) and _valid_entry(e)},
                {d: int(n) for d, n in daily.items() if isinstance(d, str)},
                [v for v in visits if _valid_visit(v)])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unexpected shape: {exc}") from exc


class SightingLog:
    def __init__(self, path: Path | str, max_airframes: int = MAX_AIRFRAMES, save_interval: float = SAVE_INTERVAL_SECONDS) -> None:
        self._path = Path(path)
        self._max = max_airframes
        self._save_interval = save_interval
        self._airframes: dict[str, dict[str, Any]] = {}
        self._daily: dict[str, int] = {}
        self._visits: list[dict[str, Any]] = []
        self._latest_visit: dict[str, dict[str, Any]] = {}
        self._loaded = False
        self._dirty = False         # a change worth a debounced write (a visit, an airframe, a detail)
        self._unsaved = False       # anything at all not on disk (a last_seen), written at shutdown
        self._last_save = 0.0

    # -- persistence ------------------------------------------------------------

    def load(self) -> None:
        self._loaded = True
        if not self._path.exists():
            return
        try:
            self._airframes, self._daily, self._visits = parse(self._path.read_bytes())
            self._visits = _trim_visits(self._visits, time_now=datetime.now().timestamp())
            self._index_visits()
        except (OSError, ValueError) as exc:
            broken = self._path.with_suffix(".json.broken")
            log.error("sightings log %s is unusable (%s); moved to %s, starting empty", self._path, exc, broken)
            try:
                os.replace(self._path, broken)
            except OSError:
                pass
            self._airframes, self._daily, self._visits = {}, {}, []
            self._latest_visit = {}

    # -- backup -------------------------------------------------------------------

    def export(self) -> bytes | None:
        """The log as it would be on disk, or None when there is nothing yet. Taken from
        memory rather than the file, so the visits since the last debounced write are in it."""
        if not self._loaded:
            self.load()
        if not self._airframes and not self._daily and not self._visits:
            return None
        return self._encode()

    def replace(self, content: bytes) -> None:
        """Take a log back from a backup: it becomes the state in memory and on disk in one
        step, so the poll cannot write the old one over it in between. Raises ``ValueError``."""
        airframes, daily, visits = parse(content)
        self._airframes, self._daily = _trimmed(airframes, self._max), _recent_days(daily)
        self._visits = _trim_visits(visits, time_now=datetime.now().timestamp())
        self._index_visits()
        self._loaded = True
        self._dirty = self._unsaved = True
        self.flush()
        if self._unsaved:
            raise ValueError(f"could not write {self._path}")

    def _encode(self) -> bytes:
        return (json.dumps({"version": 1, "airframes": self._airframes, "daily": self._daily, "visits": self._visits}) + "\n").encode()

    def flush(self) -> None:
        """Write now if anything is not on disk (call on shutdown): the last_seen of a flyover
        still in progress is what keeps it from counting as a new visit after the restart."""
        if not self._unsaved:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_bytes(self._encode())
            os.replace(tmp, self._path)
            self._dirty = self._unsaved = False
        except OSError as exc:
            log.warning("could not save sightings log %s: %s", self._path, exc)

    def _maybe_save(self, now: float) -> None:
        if self._dirty and now - self._last_save >= self._save_interval:
            self.flush()
            self._last_save = now

    # -- recording ---------------------------------------------------------------

    def record(self, aircraft: list[dict[str, Any]], now: float, today: str, gap_seconds: float = VISIT_GAP_SECONDS,
               hour: int | None = None) -> list[dict[str, Any]]:
        """Note every aircraft in ``aircraft``; returns them with ``sightings`` and ``first_seen`` added."""
        if not self._loaded:
            self.load()
        hour = datetime.fromtimestamp(now).hour if hour is None else hour
        airframes = dict(self._airframes)
        out, new_visits = [], 0
        for ac in aircraft:
            hex_ = ac.get("hex")
            if not hex_:
                out.append(ac)
                continue
            prev = airframes.get(hex_)
            new_visit = prev is None or now - prev["last_seen"] > gap_seconds
            entry = _entry_from(ac, prev, now, new_visit)
            airframes[hex_] = entry
            new_visits += new_visit
            if new_visit or _details_changed(prev, entry):
                self._dirty = True
            visit = self._latest_visit.get(hex_)
            if new_visit:
                visit = {"hex": hex_, "started_at": now, "day": today, "hour": hour}
                altitude = ac.get("altitude_ft")
                if isinstance(altitude, (int, float)) and altitude >= 0:
                    visit["max_altitude_ft"] = int(altitude)
                self._visits.append(visit)
                self._latest_visit[hex_] = visit
                self._dirty = True
            elif visit is None or visit.get("started_at") != entry["visit_started_at"]:
                visit = None
            else:
                altitude = ac.get("altitude_ft")
                if isinstance(altitude, (int, float)) and altitude >= 0 and altitude > visit.get("max_altitude_ft", -1):
                    visit["max_altitude_ft"] = int(altitude)
                    self._dirty = True
            out.append({**ac, "sightings": entry["count"], "first_seen": entry["first_seen"]})
        self._airframes = _trimmed(airframes, self._max)
        self._visits = _trim_visits(self._visits, now)
        self._index_visits()
        self._unsaved = self._unsaved or bool(aircraft)
        if new_visits:
            self._daily = _recent_days({**self._daily, today: self._daily.get(today, 0) + new_visits})
        self._maybe_save(now)
        return out

    def record_route(self, hex_: str, route: str | None, now: float, gap_seconds: float = VISIT_GAP_SECONDS) -> None:
        if not self._loaded:
            self.load()
        route = (route or "").strip().upper()
        entry = self._airframes.get(hex_)
        visit = self._latest_visit.get(hex_)
        if not route or not entry or not visit or visit.get("started_at") != entry.get("visit_started_at"):
            return
        if now < visit["started_at"] or now - entry.get("last_seen", 0) > gap_seconds or visit.get("route"):
            return
        visit["route"] = route
        self._dirty = self._unsaved = True
        self._maybe_save(now)

    def _index_visits(self) -> None:
        self._latest_visit = {}
        for visit in self._visits:
            self._latest_visit[visit["hex"]] = visit

    def stats(self, today: str) -> dict[str, Any]:
        if not self._loaded:
            self.load()
        ranked = sorted(self._airframes.items(), key=lambda kv: (-kv[1]["count"], -kv[1]["last_seen"]))
        return {
            "airframes": len(self._airframes),
            "sightings": sum(e["count"] for e in self._airframes.values()),
            "today": self._daily.get(today, 0),
            "since": min((e["first_seen"] for e in self._airframes.values() if e.get("first_seen") is not None), default=None),
            "regulars": [{k: v for k, v in {"hex": h, **e}.items() if k in REGULAR_FIELDS} for h, e in ranked[:TOP_N]],
        }

    def airframes(self) -> list[dict[str, Any]]:
        """All retained airframes, newest sighting first."""
        if not self._loaded:
            self.load()
        ranked = sorted(self._airframes.items(), key=lambda kv: (-kv[1]["last_seen"], -kv[1]["count"]))
        internal = {"visit_started_at", "operator_lookup_at", "operator_lookup_day"}
        return [{"hex": hex_, **{key: value for key, value in entry.items() if key not in internal}}
                for hex_, entry in ranked]

    def operator_lookups_today(self, today: str) -> int:
        if not self._loaded:
            self.load()
        return sum(entry.get("operator_lookup_day") == today for entry in self._airframes.values())

    def next_operator_lookup(self, now: float, retry_seconds: float) -> tuple[str, str] | None:
        if not self._loaded:
            self.load()
        pending = ((hex_, entry) for hex_, entry in self._airframes.items()
                   if entry.get("callsign") and not entry.get("operator")
                   and now - entry.get("operator_lookup_at", 0) >= retry_seconds)
        candidate = max(pending, key=lambda item: item[1]["last_seen"], default=None)
        return (candidate[0], candidate[1]["callsign"]) if candidate else None

    def mark_operator_lookup(self, hex_: str, now: float, today: str) -> bool:
        if not self._loaded:
            self.load()
        entry = self._airframes.get(hex_)
        if entry is None:
            return False
        entry["operator_lookup_at"] = now
        entry["operator_lookup_day"] = today
        self._dirty = self._unsaved = True
        self.flush()
        return not self._unsaved

    def fill_operator(self, hex_: str, operator: str | None, now: float) -> None:
        if not self._loaded:
            self.load()
        entry = self._airframes.get(hex_)
        if entry is None or entry.get("operator") or not operator:
            return
        entry["operator"] = operator
        self._dirty = self._unsaved = True
        self._maybe_save(now)

    def history(self, now: float | None = None) -> dict[str, Any]:
        if not self._loaded:
            self.load()
        now = now if now is not None else datetime.now().timestamp()
        recent = [visit for visit in self._visits if visit["started_at"] >= now - VISIT_HISTORY_DAYS * 86400]
        hours = [0] * 24
        routes: dict[str, int] = {}
        recent_days = sorted({visit["day"] for visit in recent})[-14:]
        for visit in recent:
            hours[visit["hour"]] += 1
            if visit.get("route"):
                routes[visit["route"]] = routes.get(visit["route"], 0) + 1
        return {
            "visits": len(recent),
            "truncated": len(self._visits) >= VISIT_HISTORY_KEEP,
            "last_7_days": sum(visit["started_at"] >= now - 7 * 86400 for visit in recent),
            "last_30_days": sum(visit["started_at"] >= now - 30 * 86400 for visit in recent),
            "days": [{"date": day, "visits": self._daily.get(day, 0)} for day in recent_days],
            "hours": [{"hour": hour, "visits": count} for hour, count in enumerate(hours)],
            "routes": [{"route": route, "visits": count} for route, count in sorted(routes.items(), key=lambda item: (-item[1], item[0]))[:10]],
            "highest_altitude": [
                {"hex": hex_, **{k: entry[k] for k in ("registration", "type", "max_altitude_ft", "max_altitude_at") if k in entry}}
                for hex_, entry in sorted(self._airframes.items(), key=lambda item: item[1].get("max_altitude_ft", -1), reverse=True)
                if entry.get("max_altitude_ft") is not None
            ][:10],
        }


def _details_changed(prev: dict[str, Any] | None, entry: dict[str, Any]) -> bool:
    """A registration, type or operator that just became known is worth writing down."""
    return prev is not None and any(entry.get(k) != prev.get(k) for k in ("registration", "type", "operator", "callsign", "max_altitude_ft"))


def _valid_visit(visit: Any) -> bool:
    return (isinstance(visit, dict) and isinstance(visit.get("hex"), str)
            and isinstance(visit.get("started_at"), (int, float))
            and isinstance(visit.get("day"), str) and isinstance(visit.get("hour"), int)
            and 0 <= visit["hour"] <= 23)


def _trim_visits(visits: list[dict[str, Any]], time_now: float) -> list[dict[str, Any]]:
    cutoff = time_now - VISIT_HISTORY_DAYS * 86400
    return [v for v in visits if v["started_at"] >= cutoff][-VISIT_HISTORY_KEEP:]


def _trimmed(airframes: dict[str, dict[str, Any]], limit: int) -> dict[str, dict[str, Any]]:
    """Drop the least interesting airframes (fewest visits, longest ago) once past ``limit``."""
    if len(airframes) <= limit:
        return airframes
    keep = sorted(airframes.items(), key=lambda kv: (-kv[1]["count"], -kv[1]["last_seen"]))[:limit]
    return dict(keep)


def _recent_days(daily: dict[str, int]) -> dict[str, int]:
    return dict(sorted(daily.items())[-DAILY_KEEP:])
