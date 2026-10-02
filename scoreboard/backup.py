"""One file that holds everything a user would have to redo by hand: settings and their data.

A backup is a zip:

    manifest.json           format, app and config version, when and where it was made
    config.json             the config document as stored (plugin sections hold only what was set)
    data/<source>/<file>    what a source keeps for the user — uploaded holiday pictures,
                            the flight sightings log

The data part is the sources' own: one that keeps files nothing can re-download implements
:class:`UserData` and its files ride along under its key. Restoring hands each file back to
the same source, which validates it the way it validates an upload — nothing in an archive
is copied onto the disk as it came. A file for a source that is not loaded (a sport in
follower mode, a plugin since removed) is reported as skipped, not written somewhere for
later: the directory layout is the source's business, not this module's.

The config is restored last, through the store's ``replace``: the document is checked
first, so a backup whose settings do not validate changes nothing at all, and the
``web`` section is kept from the running config for the reason ``reset`` keeps it.
"""
from __future__ import annotations

import io
import json
import logging
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import ValidationError

from . import __version__
from .config import AppConfig, ConfigStore
from .config.store import migrate

log = logging.getLogger(__name__)

FORMAT = 1
MANIFEST = "manifest.json"
CONFIG_MEMBER = "config.json"
DATA_PREFIX = "data/"
# ``web`` holds how the box is reached; a backup from another box, or an old one, must
# not take the page away from the browser that is restoring it.
KEPT_ON_RESET = ("web",)
# Sections whose change only takes effect on a restart (docs/USER_GUIDE.md, "When a change takes effect").
RESTART_SECTIONS = ("display", "follower")

MAX_ARCHIVE_BYTES = 32 * 1024 * 1024        # the upload, as received
MAX_MEMBER_BYTES = 16 * 1024 * 1024         # one file, uncompressed, as the zip header claims it
MAX_TOTAL_BYTES = 64 * 1024 * 1024          # every file together: a small archive must stay small when opened


@runtime_checkable
class UserData(Protocol):
    """A source that keeps files for the user. Both are called from a worker thread while
    the source runs, so an implementation owns the hand-over to its own state."""

    def export_data(self) -> dict[str, bytes]:
        """``{file name: content}`` — the files worth keeping, by a plain name (no directories)."""
        ...

    def import_data(self, name: str, content: bytes) -> None:
        """Take one file back. Raise ``ValueError`` with a message for the user to refuse it."""
        ...


class ArchiveError(ValueError):
    """The archive cannot be used at all. The message is written for the user."""


@dataclass
class Report:
    config_restored: bool = False
    config_from_version: int | None = None
    restored: dict[str, int] = field(default_factory=dict)      # source key -> files taken
    skipped: list[str] = field(default_factory=list)            # "data/x/y: why"
    restart_needed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"config_restored": self.config_restored, "config_from_version": self.config_from_version,
                "restored": dict(self.restored), "skipped": list(self.skipped), "restart_needed": self.restart_needed}


def user_data(sources: dict[str, Any]) -> dict[str, UserData]:
    """The loaded sources that keep files, by key."""
    return {key: src for key, src in sources.items() if isinstance(src, UserData)}


def contents(sources: dict[str, Any]) -> dict[str, int]:
    """How many files each source would put in a backup right now (for the UI to say)."""
    out = {}
    for key, src in user_data(sources).items():
        try:
            out[key] = len(src.export_data())
        except Exception:
            log.exception("backup: %s could not list its files", key)
            out[key] = 0
    return out


def filename(hostname: str, now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    host = "".join(c for c in hostname.lower() if c.isalnum() or c == "-").strip("-") or "scoreboard"
    return f"{host}-backup-{stamp}.zip"


def build(config: AppConfig, sources: dict[str, Any], *, hostname: str = "", now: datetime | None = None) -> bytes:
    """The archive. A source that fails to list its files is left out with a log line
    rather than taking the config with it: a backup with settings only still has value."""
    manifest = {"format": FORMAT, "app_version": __version__, "config_version": config.version,
                "created": (now or datetime.now()).astimezone().isoformat(timespec="seconds"), "hostname": hostname}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST, json.dumps(manifest, indent=2) + "\n")
        zf.writestr(CONFIG_MEMBER, json.dumps(config.model_dump(mode="json"), indent=2) + "\n")
        for key, src in user_data(sources).items():
            try:
                files = src.export_data()
            except Exception:
                log.exception("backup: %s could not export its files; left out", key)
                continue
            for name, content in sorted(files.items()):
                if _plain_name(name):
                    zf.writestr(f"{DATA_PREFIX}{key}/{name}", content)
    return buffer.getvalue()


def restore(archive: bytes, config: ConfigStore, sources: dict[str, Any], *, keep: tuple[str, ...] = KEPT_ON_RESET) -> Report:
    """Put a backup back. Raises :class:`ArchiveError` when nothing can be done with it;
    one bad data file is skipped and the rest goes ahead."""
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise ArchiveError(f"a backup must be under {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB")
    try:
        zf = zipfile.ZipFile(io.BytesIO(archive))
    except zipfile.BadZipFile as exc:
        raise ArchiveError("that file is not a scoreboard backup (not a zip)") from exc
    with zf:
        members = {info.filename: info for info in zf.infolist() if not info.is_dir()}
        if CONFIG_MEMBER not in members:
            raise ArchiveError("that file is not a scoreboard backup (no config.json inside)")
        _check_sizes(members.values())
        try:
            document = json.loads(zf.read(members[CONFIG_MEMBER]))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ArchiveError(f"the backup's config.json is not valid JSON: {exc}") from exc
        if not isinstance(document, dict):
            raise ArchiveError("the backup's config.json is not a settings document")
        # Checked before a single data file is written, so a backup whose settings are
        # refused leaves the box exactly as it was.
        before = config.get()
        prepared = {**migrate(document), **{s: getattr(before, s).model_dump(mode="json") for s in keep if hasattr(before, s)}}
        try:
            AppConfig.model_validate(prepared)
        except ValidationError as exc:
            raise ArchiveError("the backup's settings do not validate: "
                               + "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())) from exc

        report = Report(config_from_version=_version_of(document))
        loaded = user_data(sources)
        for name, info in members.items():
            if name in (MANIFEST, CONFIG_MEMBER):
                continue
            target = _data_target(name)
            if target is None:
                report.skipped.append(f"{name}: not a backup file")
                continue
            key, file = target
            src = loaded.get(key)
            if src is None:
                report.skipped.append(f"{name}: the {key} source is not loaded")
                continue
            try:
                src.import_data(file, zf.read(info))
            except ValueError as exc:
                report.skipped.append(f"{name}: {exc}")
                continue
            except Exception:
                log.exception("backup: %s refused %s", key, file)
                report.skipped.append(f"{name}: the {key} source could not take it")
                continue
            report.restored[key] = report.restored.get(key, 0) + 1

    after = config.replace(document, keep=keep)
    report.config_restored = True
    report.restart_needed = restart_needed(before, after)
    return report


def restart_needed(before: AppConfig, after: AppConfig) -> bool:
    """True when what changed only takes effect on a restart."""
    return any(getattr(before, s) != getattr(after, s) for s in RESTART_SECTIONS)


def _version_of(document: dict[str, Any]) -> int | None:
    try:
        return int(document.get("version") or 1)
    except (TypeError, ValueError):
        return None


def _check_sizes(members) -> None:
    total = 0
    for info in members:
        if info.file_size > MAX_MEMBER_BYTES:
            raise ArchiveError(f"{info.filename} in the backup is too large")
        total += info.file_size
    if total > MAX_TOTAL_BYTES:
        raise ArchiveError("the backup is too large once opened")


def _plain_name(name: str) -> bool:
    """A file name and nothing else: no directories, nothing hidden, nothing that is a path trick."""
    return bool(name) and "/" not in name and "\\" not in name and not name.startswith(".") and name not in ("..",)


def _data_target(member: str) -> tuple[str, str] | None:
    """``data/<key>/<file>`` -> (key, file), or None for anything else. The key and the
    file are both single path components; the source is what decides whether the file
    name is acceptable beyond that."""
    if not member.startswith(DATA_PREFIX):
        return None
    key, sep, file = member[len(DATA_PREFIX):].partition("/")
    if not sep or not _plain_name(key) or not _plain_name(file):
        return None
    return key, file


__all__ = ["KEPT_ON_RESET", "ArchiveError", "Report", "UserData", "build", "contents", "filename", "restart_needed", "restore"]
