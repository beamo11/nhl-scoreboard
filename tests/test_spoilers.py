from scoreboard.data import Event, SnapshotStore
from scoreboard.nhl.source import NhlConfig
from scoreboard.spoilers import presentation_snapshot, spoiler_event


def _game(away="TOR", home="BOS", sport="nhl"):
    return {
        "id": 123,
        "sport": sport,
        "phase": "live",
        "state": "LIVE",
        "date": "2026-11-02",
        "start_time_utc": "2026-11-02T23:00:00Z",
        "period": "2nd",
        "clock": "12:34",
        "outcome": "",
        "away": {"abbrev": away, "score": 2, "sog": 18},
        "home": {"abbrev": home, "score": 1, "sog": 16},
        "goals": [{"scorer": "Player"}],
    }


def test_spoiler_team_picker_schema_and_validation():
    config = NhlConfig(spoiler_teams=["tor"])
    field = NhlConfig.model_json_schema()["properties"]["spoiler_teams"]

    assert config.spoiler_teams == ["TOR"]
    assert field["x-widget"] == "team-picker"
    assert "TOR" in field["items"]["enum"]


def test_presentation_snapshot_hides_matching_scores_and_keeps_schedule():
    store = SnapshotStore()
    game = _game()
    other = _game("MTL", "OTT")
    store.publish("nhl.scores", [game, other])
    store.publish("nhl.schedule", [game])
    store.publish("nhl.main_event", game)
    store.publish("main_event", game)
    original = store.get()

    masked = presentation_snapshot(original, {"TOR"})

    hidden = masked.get("nhl.scores")[0]
    assert hidden["phase"] == "pregame" and hidden["state"] == "FUT"
    assert hidden["start_time_utc"] == game["start_time_utc"]
    assert "score" not in hidden["away"] and "score" not in hidden["home"]
    assert "sog" not in hidden["away"] and "goals" not in hidden
    assert masked.get("nhl.scores")[1] is other
    assert masked.get("main_event")["phase"] == "pregame"
    assert original.get("nhl.scores")[0]["away"]["score"] == 2
    assert original.get("main_event")["away"]["score"] == 2


def test_spoiler_event_hides_events_from_a_selected_team_game():
    game = _game()
    opponent_goal = Event("nhl.goal", team="BOS", payload={"game": game})
    other_game_goal = Event("nhl.goal", team="MTL", payload={"game": _game("MTL", "OTT")})

    assert spoiler_event(opponent_goal, {"TOR"})
    assert not spoiler_event(other_game_goal, {"TOR"})
    assert not spoiler_event(Event("nfl.touchdown", team="TOR"), {"TOR"})