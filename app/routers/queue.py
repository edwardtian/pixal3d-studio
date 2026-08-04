from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.queue import task_queue
from app.auth import get_current_user
from app.models import User, Task
from app.schemas import QueueStatus
from app.database import get_db
from app.gpu import worker_manager, detect_all_gpus, gpu_is_available
from app.config import settings

router = APIRouter(prefix="/api/queue", tags=["queue"])


@router.get("/status", response_model=QueueStatus)
async def queue_status(user: User = Depends(get_current_user)):
    status = await task_queue.get_status()
    your_pos = await task_queue.get_user_position(user.id)

    # Per-GPU state
    enabled_ids = await worker_manager.enabled_ids()
    physical = detect_all_gpus()
    min_free_mb = int(settings.GPU_MIN_FREE_VRAM_GB * 1024)
    gpus = []
    for g in physical:
        gpus.append({
            "id": g["id"],
            "name": g["name"],
            "mem_total_mb": g["mem_total_mb"],
            "mem_used_mb": g["mem_used_mb"],
            "mem_free_mb": g["mem_free_mb"],
            "utilization_pct": g["utilization_pct"],
            "enabled": g["id"] in enabled_ids,
            "available": g["id"] in enabled_ids and gpu_is_available(g["id"], min_free_mb=min_free_mb),
        })

    return QueueStatus(
        total_waiting=status["total_waiting"],
        gpu_busy=status["num_busy"] > 0,
        num_busy=status["num_busy"],
        your_position=your_pos,
        gpus=gpus,
        active_tasks=[],
    )


@router.get("/tasks")
async def queue_tasks(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Return the caller's queued + in-progress tasks (admins see all)."""
    active_ids = await task_queue.get_active_task_ids()
    if not active_ids:
        return []

    query = select(Task, User.username).join(User, Task.user_id == User.id).where(Task.id.in_(active_ids))
    if user.role != "admin":
        query = query.where(Task.user_id == user.id)
    query = query.order_by(Task.created_at.asc())

    result = await db.execute(query)
    rows = result.all()

    out = []
    for task, username in rows:
        pos = await task_queue.get_position(task.id) if task.status == "queued" else None
        out.append({
            "id": task.id,
            "user_id": task.user_id,
            "username": username,
            "status": task.status,
            "subtask_index": task.subtask_index or 0,
            "subtask_total": task.subtask_total or 6,
            "subtask_name": task.subtask_name or "",
            "subtask_step": task.subtask_step or 0,
            "subtask_total_steps": task.subtask_total_steps or 0,
            "overall_progress": task.overall_progress or 0,
            "assigned_gpu": task.assigned_gpu,
            "queue_position": pos,
            "created_at": task.created_at.isoformat() if task.created_at else None,
        })
    return out
