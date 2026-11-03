"""Presentation-only score spoiler filtering."""
from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType
from typing import Any

from .config.models import AppConfig
from .data.events import Event
from .data.store import Snapshot


def configured_spoiler_teams(config: AppConfig) -> frozenset[str]:
	raw = config.sources.get("nhl", {}).get("spoiler_teams", ())
	if not isinstance(raw, (list, tuple, set, frozenset)):
		return frozenset()
	return _team_codes(raw)


def presentation_snapshot(snapshot: Snapshot, teams: Iterable[str]) -> Snapshot:
	selected = _team_codes(teams)
	if not selected:
		return snapshot

	data = dict(snapshot.data)
	changed = False
	for key in ("nhl.scores", "nhl.schedule", "nhl.main_event"):
		value = data.get(key)
		if isinstance(value, list):
			masked = [spoiler_game(game, selected) for game in value]
			if any(a is not b for a, b in zip(masked, value)):
				data[key] = masked
				changed = True
		elif isinstance(value, dict):
			masked = spoiler_game(value, selected)
			if masked is not value:
				data[key] = masked
				changed = True

	main = data.get("main_event")
	if isinstance(main, dict) and main.get("sport") == "nhl":
		masked = spoiler_game(main, selected)
		if masked is not main:
			data["main_event"] = masked
			changed = True

	if not changed:
		return snapshot
	return Snapshot(snapshot.version, MappingProxyType(data), snapshot.updated, snapshot.versions)


def spoiler_game(game: Any, teams: Iterable[str]) -> Any:
	if not isinstance(game, dict) or not _matches(game, _team_codes(teams)):
		return game

	masked = dict(game)
	for side in ("away", "home"):
		team = game.get(side)
		if isinstance(team, dict):
			hidden_team = dict(team)
			hidden_team.pop("score", None)
			hidden_team.pop("sog", None)
			masked[side] = hidden_team
	masked.update({"phase": "pregame", "state": "FUT", "period": "", "clock": "", "outcome": ""})
	masked["in_intermission"] = False
	for key in ("goals", "penalties", "powerplay", "pulled_goalie"):
		masked.pop(key, None)
	return masked


def spoiler_event(event: Event, teams: Iterable[str]) -> bool:
	selected = _team_codes(teams)
	if not selected or not event.kind.startswith("nhl."):
		return False
	if _matches(event.payload.get("game"), selected):
		return True
	return event.team is not None and event.team.strip().upper() in selected


def _matches(game: Any, teams: frozenset[str]) -> bool:
	if not isinstance(game, dict):
		return False
	return any(
		isinstance(game.get(side), dict)
		and str(game[side].get("abbrev", "")).strip().upper() in teams
		for side in ("away", "home")
	)


def _team_codes(teams: Iterable[str]) -> frozenset[str]:
	return frozenset(str(team).strip().upper() for team in teams if str(team).strip())