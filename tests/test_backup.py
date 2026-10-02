"""Backup and restore: the archive, the sources' files in it, the config copies on disk, and the endpoints."""

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from scoreboard import backup
from scoreboard.boards.clock import ClockBoard
from scoreboard.config import AppConfig, ConfigStore
from scoreboard.data import SnapshotStore
from scoreboard.data.events import EventBus
from scoreboard.director import Director
from scoreboard.extras.flights.sightings import SightingLog
from scoreboard.extras.flights.source import FlightsSource
from scoreboard.extras.holidays import images
from scoreboard.extras.holidays.source import HolidaysSource
from scoreboard.output import PreviewHub
from scoreboard.plugins import Registry
from scoreboard.web.api import create_app
from scoreboard.web.guard import UI_HEADER, UI_TOKEN

UI = {"headers": {UI_HEADER: UI_TOKEN}, "base_url": "http://localhost"}


class Notes:
    """A source that keeps files: the smallest possible UserData."""

    key = "notes"

    def __init__(self):
        self.files = {"a.txt": b"alpha"}

    def export_data(self):
        return dict(self.files)

    def import_data(self, name, content):
        if not name.endswith(".txt"):
            raise ValueError("only text files")
        self.files[name] = content


class Mute:
    """A source with no files of its own: not UserData, and never asked."""

    key = "mute"


def members(archive: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        return {i.filename: zf.read(i) for i in zf.infolist()}


def archive_of(config: dict, data: dict[str, bytes] | None = None, *, with_config=True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": 1}))
        if with_config:
            zf.writestr("config.json", json.dumps(config))
        for name, content in (data or {}).items():
            zf.writestr(name, content)
    return buf.getvalue()


def png(size=(8, 8)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, (255, 0, 0, 255)).save(buf, "PNG")
    return buf.getvalue()


# -- the archive ------------------------------------------------------------------


def test_build_holds_manifest_config_and_each_sources_files(config_store):
    config_store.update({"brightness": {"day": 42}})
    got = members(backup.build(config_store.get(), {"notes": Notes(), "mute": Mute()}, hostname="office"))
    assert set(got) == {"manifest.json", "config.json", "data/notes/a.txt"}
    manifest = json.loads(got["manifest.json"])
    assert manifest["format"] == backup.FORMAT and manifest["hostname"] == "office" and manifest["config_version"] == AppConfig().version
    assert json.loads(got["config.json"])["brightness"]["day"] == 42
    assert got["data/notes/a.txt"] == b"alpha"


def test_build_survives_a_source_that_cannot_list_its_files(config_store):
    class Broken(Notes):
        def export_data(self):
            raise OSError("disk gone")

    got = members(backup.build(config_store.get(), {"notes": Broken()}))
    assert set(got) == {"manifest.json", "config.json"}


def test_filename_is_host_and_stamp():
    from datetime import datetime
    assert backup.filename("Office Pi.local", datetime(2026, 10, 1, 9, 30, 5)) == "officepilocal-backup-20261001-093005.zip"
    assert backup.filename("", datetime(2026, 10, 1)).startswith("scoreboard-backup-")


def test_restore_round_trips_config_and_data(config_store):
    src = Notes()
    src.files = {"a.txt": b"alpha", "b.txt": b"beta"}
    config_store.update({"brightness": {"day": 42}})
    archive = backup.build(config_store.get(), {"notes": src})

    config_store.update({"brightness": {"day": 10}})
    fresh = Notes()
    fresh.files = {}
    report = backup.restore(archive, config_store, {"notes": fresh})
    assert config_store.get().brightness.day == 42
    assert fresh.files == {"a.txt": b"alpha", "b.txt": b"beta"}
    assert report.config_restored and report.restored == {"notes": 2} and report.skipped == []
    assert report.restart_needed is False


def test_restore_keeps_the_web_section_of_the_running_config(config_store):
    config_store.update({"web": {"port": 9999}})
    archive = archive_of({**AppConfig().model_dump(mode="json"), "web": {"port": 1234, "allowed_hosts": ["elsewhere"]}})
    backup.restore(archive, config_store, {})
    assert config_store.get().web.port == 9999


def test_restore_says_when_a_restart_is_needed(config_store):
    doc = AppConfig().model_dump(mode="json")
    doc["display"]["width"] = 64
    assert backup.restore(archive_of(doc), config_store, {}).restart_needed is True


def test_restore_migrates_an_old_export(config_store):
    old = {**AppConfig().model_dump(mode="json"), "version": 1, "sources": {"holidays": {"disabled": ["Boxing Day"]}}}
    report = backup.restore(archive_of(old), config_store, {})
    assert report.config_from_version == 1
    assert config_store.get().sources["holidays"]["overrides"]["Boxing Day"] == {"enabled": False}


def test_a_bad_config_changes_nothing_at_all(config_store):
    src = Notes()
    before = config_store.path.read_text()
    bad = {**AppConfig().model_dump(mode="json"), "brightness": {"day": 500}}
    with pytest.raises(backup.ArchiveError, match=r"brightness\.day"):
        backup.restore(archive_of(bad, {"data/notes/z.txt": b"zeta"}), config_store, {"notes": src})
    assert config_store.path.read_text() == before
    assert "z.txt" not in src.files                             # the data was not touched either


@pytest.mark.parametrize("archive, message", [
    (b"not a zip", "not a zip"),
    (archive_of({}, with_config=False), "no config.json"),
    (archive_of([1, 2]), "not a settings document"),
])
def test_things_that_are_not_backups_are_refused(config_store, archive, message):
    with pytest.raises(backup.ArchiveError, match=message):
        backup.restore(archive, config_store, {})


def test_a_corrupt_config_member_is_refused(config_store):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("config.json", "{not json")
    with pytest.raises(backup.ArchiveError, match="not valid JSON"):
        backup.restore(buf.getvalue(), config_store, {})


def test_a_source_may_refuse_one_file_and_the_rest_goes_ahead(config_store):
    src = Notes()
    data = {"data/notes/ok.txt": b"fine", "data/notes/bad.exe": b"nope"}
    report = backup.restore(archive_of(AppConfig().model_dump(mode="json"), data), config_store, {"notes": src})
    assert src.files["ok.txt"] == b"fine" and "bad.exe" not in src.files
    assert report.restored == {"notes": 1} and report.skipped == ["data/notes/bad.exe: only text files"]


def test_a_source_that_crashes_on_a_file_is_reported_not_raised(config_store):
    class Cranky(Notes):
        def import_data(self, name, content):
            raise RuntimeError("boom")

    report = backup.restore(archive_of(AppConfig().model_dump(mode="json"), {"data/notes/a.txt": b"x"}), config_store, {"notes": Cranky()})
    assert report.config_restored and report.skipped == ["data/notes/a.txt: the notes source could not take it"]


def test_files_for_sources_not_loaded_and_odd_paths_are_skipped(config_store):
    src = Notes()
    data = {
        "data/gone/a.txt": b"x",                 # no such source
        "data/notes/sub/a.txt": b"x",            # a directory inside the source's files
        "data/../notes/a.txt": b"x",             # a path trick
        "data/notes/.hidden.txt": b"x",
        "README": b"x",
    }
    report = backup.restore(archive_of(AppConfig().model_dump(mode="json"), data), config_store, {"notes": src})
    assert src.files == {"a.txt": b"alpha"}
    assert report.restored == {}
    assert sorted(report.skipped) == sorted([
        "data/gone/a.txt: the gone source is not loaded",
        "data/notes/sub/a.txt: not a backup file",
        "data/../notes/a.txt: not a backup file",
        "data/notes/.hidden.txt: not a backup file",
        "README: not a backup file",
    ])


def test_an_archive_that_would_open_too_large_is_refused(config_store, monkeypatch):
    monkeypatch.setattr(backup, "MAX_MEMBER_BYTES", 16)
    with pytest.raises(backup.ArchiveError, match="too large"):
        backup.restore(archive_of(AppConfig().model_dump(mode="json")), config_store, {})
    monkeypatch.setattr(backup, "MAX_MEMBER_BYTES", 10_000)
    monkeypatch.setattr(backup, "MAX_ARCHIVE_BYTES", 8)
    with pytest.raises(backup.ArchiveError, match="under 0 MB"):
        backup.restore(archive_of(AppConfig().model_dump(mode="json")), config_store, {})


def test_contents_counts_what_each_source_would_put_in(config_store):
    assert backup.contents({"notes": Notes(), "mute": Mute()}) == {"notes": 1}


# -- the bundled sources' files -------------------------------------------------------


def test_holidays_export_and_import_their_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(images, "USER_IMAGES", tmp_path / "holidays")
    src = HolidaysSource()
    assert src.export_data() == {}
    images.save("christmas_day", png((300, 200)))
    exported = src.export_data()
    assert list(exported) == ["christmas_day.png"]

    monkeypatch.setattr(images, "USER_IMAGES", tmp_path / "elsewhere")
    src.import_data("christmas_day.png", exported["christmas_day.png"])
    assert (tmp_path / "elsewhere" / "christmas_day.png").exists()
    with pytest.raises(ValueError, match="not a picture file"):
        src.import_data("christmas_day.jpg", exported["christmas_day.png"])
    with pytest.raises(ValueError, match="lowercase"):
        src.import_data("Bad Name.png", exported["christmas_day.png"])
    with pytest.raises(ValueError):
        src.import_data("boxing_day.png", b"not a picture")


def test_flights_export_and_import_the_sightings_log(tmp_path):
    log = SightingLog(tmp_path / "s.json")
    src = FlightsSource(sightings=log)
    assert src.export_data() == {}                                                  # nothing seen yet
    log.record([{"hex": "a1", "registration": "C-GABC", "type": "C172", "operator": "", "lat": 1, "lon": 2}], 1_700_000_000.0, "2026-09-04")
    exported = src.export_data()
    assert set(exported) == {"sightings.json"}
    assert json.loads(exported["sightings.json"])["airframes"]["a1"]["count"] == 1    # from memory: not yet flushed to disk

    other = SightingLog(tmp_path / "t.json")
    FlightsSource(sightings=other).import_data("sightings.json", exported["sightings.json"])
    assert other.stats("2026-09-04")["airframes"] == 1
    assert json.loads((tmp_path / "t.json").read_text())["airframes"]["a1"]["count"] == 1   # and on disk at once
    with pytest.raises(ValueError, match="not a sightings log"):
        FlightsSource(sightings=other).import_data("other.json", b"{}")
    with pytest.raises(ValueError, match="unexpected shape"):
        FlightsSource(sightings=other).import_data("sightings.json", b"[]")


# -- the config copies ----------------------------------------------------------------


def test_store_lists_and_restores_its_copies(config_store):
    assert config_store.backups() == []
    config_store.update({"brightness": {"day": 11}})           # copy 1: defaults
    config_store.update({"brightness": {"day": 22}})           # copy 1: day 11, copy 2: defaults
    assert [b["slot"] for b in config_store.backups()] == [1, 2]
    assert config_store.read_backup(1)["brightness"]["day"] == 11
    config_store.restore_backup(2)
    assert config_store.get().brightness.day == AppConfig().brightness.day
    assert config_store.read_backup(1)["brightness"]["day"] == 22      # the config that was replaced is now the way back
    with pytest.raises(FileNotFoundError):
        config_store.read_backup(5)
    with pytest.raises(FileNotFoundError):
        config_store.read_backup(0)


def test_store_restore_keeps_sections(config_store):
    config_store.update({"brightness": {"day": 11}})
    config_store.update({"web": {"port": 9999}})
    config_store.restore_backup(1, keep=("web",))
    assert config_store.get().web.port == 9999 and config_store.get().brightness.day == 11


def test_store_refuses_a_copy_that_is_not_a_document(config_store):
    config_store.update({"brightness": {"day": 11}})
    config_store.path.with_suffix(".json.1").write_text("[]")
    with pytest.raises(ValueError):
        config_store.read_backup(1)


# -- the endpoints -----------------------------------------------------------------


def client(tmp_path, sources=None):
    config = ConfigStore(tmp_path / "config.json")
    snapshots, events = SnapshotStore(), EventBus()
    reg = Registry(boards={"clock": ClockBoard()}, sources=sources or {})
    director = Director(config, snapshots, reg, events)
    return TestClient(create_app(config, snapshots, reg, director, PreviewHub()), **UI), config


def test_export_is_a_zip_download_with_the_config_inside(tmp_path):
    c, config = client(tmp_path, {"notes": Notes()})
    config.update({"brightness": {"day": 42}})
    r = c.get("/api/backup/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"].startswith('attachment; filename="') and "-backup-" in r.headers["content-disposition"]
    got = members(r.content)
    assert json.loads(got["config.json"])["brightness"]["day"] == 42 and got["data/notes/a.txt"] == b"alpha"


def test_status_says_what_is_in_a_backup_and_which_copies_exist(tmp_path):
    c, config = client(tmp_path, {"notes": Notes()})
    config.update({"brightness": {"day": 42}})
    st = c.get("/api/backup").json()
    assert st["contents"] == {"notes": 1}
    assert [v["slot"] for v in st["versions"]] == [1] and st["versions"][0]["bytes"] > 0


def test_import_restores_and_reports(tmp_path):
    src = Notes()
    c, config = client(tmp_path, {"notes": src})
    doc = {**AppConfig().model_dump(mode="json"), "brightness": {**AppConfig().brightness.model_dump(mode="json"), "day": 33}}
    r = c.post("/api/backup/import", content=archive_of(doc, {"data/notes/b.txt": b"beta", "data/notes/x.bin": b"?"}))
    assert r.status_code == 200
    assert r.json() == {"config_restored": True, "config_from_version": AppConfig().version, "restored": {"notes": 1},
                        "skipped": ["data/notes/x.bin: only text files"], "restart_needed": False}
    assert config.get().brightness.day == 33 and src.files["b.txt"] == b"beta"


def test_import_refuses_junk_and_oversize(tmp_path, monkeypatch):
    c, _ = client(tmp_path)
    r = c.post("/api/backup/import", content=b"hello")
    assert r.status_code == 422 and "not a zip" in r.json()["detail"]
    monkeypatch.setattr(backup, "MAX_ARCHIVE_BYTES", 4)
    assert c.post("/api/backup/import", content=b"hello").status_code == 413


def test_restore_a_copy_over_the_api(tmp_path):
    c, config = client(tmp_path)
    config.update({"brightness": {"day": 11}})
    config.update({"brightness": {"day": 22}})
    r = c.post("/api/backup/versions/1/restore")
    assert r.status_code == 200 and r.json()["restored"] == 1 and r.json()["restart_needed"] is False
    assert config.get().brightness.day == 11
    assert c.post("/api/backup/versions/5/restore").status_code == 404
    assert c.post("/api/backup/versions/9/restore").status_code == 404
    config.path.with_suffix(".json.1").write_text(json.dumps({"brightness": {"day": 999}}))
    assert c.post("/api/backup/versions/1/restore").status_code == 422


def test_restore_a_copy_needs_the_ui_header(tmp_path):
    c, config = client(tmp_path)
    config.update({"brightness": {"day": 11}})
    bare = TestClient(c.app, base_url="http://localhost")
    assert bare.post("/api/backup/versions/1/restore").status_code == 403
    assert bare.post("/api/backup/import", content=b"x").status_code == 403
