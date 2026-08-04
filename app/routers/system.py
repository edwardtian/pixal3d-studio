from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_admin_user
from app.models import User
from app.schemas import GPUsConfig, GPUsConfigResponse, GPUInfo, WorkerStatus
from app.gpu import (
    worker_manager,
    detect_all_gpus,
    _save_enabled_gpu_ids,
    _load_enabled_gpu_ids,
)
from app.config import settings

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/gpus", response_model=GPUsConfigResponse)
async def get_gpus(admin: User = Depends(get_admin_user)):
    physical = detect_all_gpus()
    enabled = await _load_enabled_gpu_ids()
    workers = await worker_manager.get_workers_status()
    return GPUsConfigResponse(
        physical=[GPUInfo(
            id=g["id"], name=g["name"],
            mem_total_mb=g["mem_total_mb"], mem_used_mb=g["mem_used_mb"],
            mem_free_mb=g["mem_free_mb"], utilization_pct=g["utilization_pct"],
            enabled=g["id"] in enabled,
        ) for g in physical],
        enabled=enabled,
        workers=[WorkerStatus(**w) for w in workers],
        min_free_vram_gb=settings.GPU_MIN_FREE_VRAM_GB,
        util_threshold=settings.GPU_UTIL_THRESHOLD,
    )


@router.put("/gpus", response_model=GPUsConfigResponse)
async def set_gpus(payload: GPUsConfig, admin: User = Depends(get_admin_user)):
    physical = detect_all_gpus()
    valid_ids = {g["id"] for g in physical}
    new_ids = [int(i) for i in payload.enabled_ids if int(i) in valid_ids]
    if not new_ids:
        raise HTTPException(status_code=400, detail="At least one valid GPU must be enabled")

    await _save_enabled_gpu_ids(new_ids)
    await worker_manager.reconfigure(new_ids)

    enabled = await _load_enabled_gpu_ids()
    workers = await worker_manager.get_workers_status()
    return GPUsConfigResponse(
        physical=[GPUInfo(
            id=g["id"], name=g["name"],
            mem_total_mb=g["mem_total_mb"], mem_used_mb=g["mem_used_mb"],
            mem_free_mb=g["mem_free_mb"], utilization_pct=g["utilization_pct"],
            enabled=g["id"] in enabled,
        ) for g in physical],
        enabled=enabled,
        workers=[WorkerStatus(**w) for w in workers],
        min_free_vram_gb=settings.GPU_MIN_FREE_VRAM_GB,
        util_threshold=settings.GPU_UTIL_THRESHOLD,
    )
