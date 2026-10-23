"""Administrator-only logical checkpoint and explicit maintenance handoff."""
import asyncio
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from src.services.brain_backup import maintenance
from src.services.brain_backup.git import BackupError

backup_router = APIRouter(prefix='/system/backup', tags=['system'])


class MaintenanceResponse(BaseModel):
    state: str
    quiesced: bool
    checkpoint: str | None = None
    dirty: bool | None = None
    remote_verified: bool = False
    pending_tasks: int = 0
    error: str | None = None


def admin(request):
    if not getattr(getattr(request.state, 'principal', None), 'is_system', False):
        raise HTTPException(403, 'FORBIDDEN')


@backup_router.get('/status', response_model=MaintenanceResponse)
async def backup_status(request: Request):
    admin(request)
    return maintenance.status()


@backup_router.post('/quiesce', response_model=MaintenanceResponse)
async def backup_quiesce(request: Request):
    admin(request)
    try:
        return await asyncio.to_thread(maintenance.quiesce)
    except BackupError as exc:
        raise HTTPException(503, str(exc)) from None


@backup_router.post('/checkpoint', response_model=MaintenanceResponse)
async def backup_checkpoint(request: Request, timeout_seconds: int = Query(120, ge=1, le=300)):
    admin(request)
    from src.services.brain_backup.__main__ import configured
    try:
        maintenance.quiesce()
        return await asyncio.to_thread(maintenance.checkpoint, configured(), timeout_seconds)
    except BackupError as exc:
        from src.services.brain_backup.state import enabled
        if enabled():
            maintenance.fail(str(exc))
        raise HTTPException(503, str(exc)) from None


@backup_router.post('/resume', response_model=MaintenanceResponse)
async def backup_resume(request: Request):
    admin(request)
    from src.services.brain_backup.__main__ import configured
    try:
        return await asyncio.to_thread(maintenance.resume, configured())
    except BackupError as exc:
        raise HTTPException(503, str(exc)) from None
