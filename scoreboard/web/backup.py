"""Endpoints behind the Backup card on the Settings page: a file with everything in it,
the way to put one back, and the config copies the store keeps on every save.

Nothing here knows what is in an archive; ``scoreboard/backup.py`` does, and the sources
own their files. This turns its errors into status codes and sets the download headers.
"""
from __future__ import annotations

import socket
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import ValidationError

from .. import backup
from ..config import ConfigStore
from ..plugins import Registry

PAYLOAD_TOO_LARGE = 413
UNPROCESSABLE = 422


async def _read_capped(request: Request) -> bytes:
    """The body, stopped at the cap as it arrives rather than buffered whole and then measured."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > backup.MAX_ARCHIVE_BYTES:
            raise HTTPException(status_code=PAYLOAD_TOO_LARGE,
                                detail=f"a backup must be under {backup.MAX_ARCHIVE_BYTES // (1024 * 1024)} MB")
        chunks.append(chunk)
    return b"".join(chunks)


def router(config: ConfigStore, registry: Registry) -> APIRouter:
    api = APIRouter(prefix="/api/backup", tags=["backup"])

    @api.get("")
    def status() -> dict[str, Any]:
        """What a backup would hold right now, and the config copies on disk."""
        return {"contents": backup.contents(registry.sources), "versions": config.backups()}

    @api.get("/export")
    def export() -> Response:
        name = backup.filename(socket.gethostname())
        return Response(content=backup.build(config.get(), registry.sources, hostname=socket.gethostname()),
                        media_type="application/zip",
                        headers={"content-disposition": f'attachment; filename="{name}"', "cache-control": "no-store"})

    @api.post("/import")
    async def import_(request: Request) -> dict[str, Any]:
        """The archive is the whole request body — no form encoding, so the browser hands
        us the File object directly, the way a holiday picture is uploaded."""
        archive = await _read_capped(request)
        try:
            return backup.restore(archive, config, registry.sources).as_dict()
        except backup.ArchiveError as exc:
            raise HTTPException(status_code=UNPROCESSABLE, detail=str(exc)) from exc

    @api.post("/versions/{slot}/restore")
    def restore_version(slot: int) -> dict[str, Any]:
        """Back to the config as it was ``slot`` saves ago. The config being replaced
        becomes slot 1, so this can be undone the same way."""
        before = config.get()
        try:
            after = config.restore_backup(slot, keep=backup.KEPT_ON_RESET)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"there is no backup in slot {slot}") from exc
        except ValueError as exc:                        # not JSON, or not an object
            raise HTTPException(status_code=UNPROCESSABLE, detail=f"backup {slot} is unreadable: {exc}") from exc
        except ValidationError as exc:
            raise HTTPException(status_code=UNPROCESSABLE, detail=exc.errors(include_url=False)) from exc
        return {"restored": slot, "restart_needed": backup.restart_needed(before, after), "versions": config.backups()}

    return api
