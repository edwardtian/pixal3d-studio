import os
import shutil
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database import get_db
from app.models import User, Task
from app.schemas import TaskResponse, RatingUpdate
from app.auth import get_current_user
from app.config import settings
from app.parameters import validate_parameters
from app.queue import task_queue

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

@router.post("/upload", response_model=dict)
async def upload_image(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    ext = os.path.splitext(file.filename or "upload.png")[1] or ".png"
    filename = f"{user.id}_{uuid.uuid4().hex[:12]}{ext}"
    filepath = settings.UPLOAD_DIR / filename
    with open(filepath, "wb") as f:
        shutil.copyfileobj(file.file, f)

    return {"filename": filename, "path": str(filepath)}


@router.post("", response_model=TaskResponse)
async def create_task(
    image_filename: str = Form(...),
    parameters: str = Form("{}"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    import json
    params = json.loads(parameters)
    params = validate_parameters(params)

    image_path = str(settings.UPLOAD_DIR / image_filename)
    if not os.path.exists(image_path):
        raise HTTPException(status_code=400, detail="Image not found. Please upload first.")

    task = Task(
        user_id=user.id,
        status="queued",
        input_image_path=image_filename,
        parameters=params,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    await task_queue.submit(task.id, user.id)

    return task

@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    user_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from datetime import datetime, timezone
    from sqlalchemy import and_

    query = select(Task, User.username).join(User, Task.user_id == User.id)

    # Non-admins can only see their own tasks
    if user.role != "admin":
        query = query.where(Task.user_id == user.id)
    elif user_id is not None:
        query = query.where(Task.user_id == user_id)

    # Date range filter
    filters = []
    if date_from:
        try:
            dt_from = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
            filters.append(Task.created_at >= dt_from)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date_from format. Use ISO 8601.")
    if date_to:
        try:
            dt_to = datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc)
            filters.append(Task.created_at <= dt_to)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date_to format. Use ISO 8601.")

    if filters:
        query = query.where(and_(*filters))

    query = query.order_by(Task.created_at.desc())
    result = await db.execute(query)
    rows = result.all()
    # Build response dicts with username included
    out = []
    for task, username in rows:
        data = {c.name: getattr(task, c.name) for c in task.__table__.columns}
        data["username"] = username
        out.append(data)
    return out


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalars().first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Not your task")
    return task


@router.get("/{task_id}/image")
async def get_task_image(
    task_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalars().first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Not your task")

    image_path = settings.UPLOAD_DIR / task.input_image_path
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="Image file not found")
    return FileResponse(str(image_path))


@router.get("/{task_id}/render/{mode}/{frame}")
async def get_render(
    task_id: int,
    mode: str,
    frame: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalars().first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Not your task")

    renders = task.render_paths or {}
    mode_files = renders.get(mode, [])
    if frame < 0 or frame >= len(mode_files):
        raise HTTPException(status_code=404, detail="Render frame not found")

    render_path = settings.RENDERS_DIR / f"task_{task_id}" / mode_files[frame]
    if not render_path.exists():
        raise HTTPException(status_code=404, detail="Render file not found")
    return FileResponse(str(render_path))


@router.get("/{task_id}/download")
async def download_glb(
    task_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalars().first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Not your task")

    if not task.output_glb_path:
        raise HTTPException(status_code=400, detail="GLB not ready")

    glb_path = settings.OUTPUT_DIR / task.output_glb_path
    if not glb_path.exists():
        raise HTTPException(status_code=404, detail="GLB file not found")
    return FileResponse(str(glb_path), media_type="model/gltf-binary", filename=f"pixal3d_task_{task_id}.glb")


@router.get("/{task_id}/download/refined")
async def download_refined_glb(
    task_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Download the refined GLB produced by a refine pass.

    Works on either the refine task itself or its parent task: if the queried
    task has no refined GLB but has a child refine task that does, serve that.
    """
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalars().first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Not your task")

    refined_path = task.output_glb_path_refined
    if not refined_path:
        # Look for a child refine task that produced a refined GLB.
        child_result = await db.execute(
            select(Task).where(
                Task.parent_task_id == task_id,
                Task.refine_status == "completed",
                Task.output_glb_path_refined != "",
            ).order_by(Task.created_at.desc())
        )
        child = child_result.scalars().first()
        if child is None:
            raise HTTPException(status_code=400, detail="No refined GLB available")
        refined_path = child.output_glb_path_refined
        task_id = child.id

    glb_path = settings.OUTPUT_DIR / refined_path
    if not glb_path.exists():
        raise HTTPException(status_code=404, detail="Refined GLB file not found")
    return FileResponse(
        str(glb_path),
        media_type="model/gltf-binary",
        filename=f"pixal3d_task_{task_id}_refined.glb",
    )


@router.get("/{task_id}/glb-url")
async def get_glb_url(
    task_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalars().first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Not your task")

    if not task.output_glb_path:
        raise HTTPException(status_code=400, detail="GLB not ready")
    return {"url": f"/api/tasks/{task_id}/download"}


@router.delete("/{task_id}")
async def delete_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalars().first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Not your task")

    if task.status in ("queued", "processing", "cancelling"):
        raise HTTPException(
            status_code=409,
            detail="Task is still queued or processing. Cancel it first.",
        )

    if task.output_glb_path:
        glb_path = settings.OUTPUT_DIR / task.output_glb_path
        if glb_path.exists():
            os.remove(glb_path)

    if task.output_glb_path_refined:
        refined_path = settings.OUTPUT_DIR / task.output_glb_path_refined
        if refined_path.exists():
            os.remove(refined_path)

    render_dir = settings.RENDERS_DIR / f"task_{task_id}"
    if render_dir.exists():
        shutil.rmtree(render_dir)

    await db.delete(task)
    await db.commit()
    return {"detail": "Task deleted"}


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalars().first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Not your task")

    if task.status not in ("queued", "processing"):
        raise HTTPException(status_code=400, detail=f"Cannot cancel task in '{task.status}' state")

    outcome = await task_queue.cancel(task_id)
    if outcome == "notfound":
        # Task may have just finished; reflect current status
        raise HTTPException(status_code=400, detail="Task is not in the queue")

    if outcome == "queued":
        task.status = "cancelled"
        task.progress = "Cancelled"
        if task.parent_task_id is not None:
            task.refine_status = "cancelled"
        task.completed_at = datetime.now(timezone.utc)
        await db.commit()
        return {"detail": "Task cancelled", "immediate": True}
    else:
        task.status = "cancelling"
        task.progress = "Cancelling..."
        await db.commit()
        return {"detail": "Cancel requested; task will stop at the next sub-task boundary", "immediate": False}


@router.post("/{task_id}/refine", response_model=TaskResponse)
async def refine_task(
    task_id: int,
    preset: str = Form("game_engine"),
    parameters: str = Form("{}"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a refine task that re-runs GLB extraction (with the full
    post-process pipeline) from the parent task's saved latent.

    The new task is queued just like a generation task; the worker dispatches
    on ``parent_task_id is not None``. The user can poll the new task's
    status/progress and download the refined GLB once ``refine_status``
    becomes ``completed``.
    """
    import json
    from app.postprocess.config import validate_refine_parameters

    result = await db.execute(select(Task).where(Task.id == task_id))
    parent = result.scalars().first()
    if not parent:
        raise HTTPException(status_code=404, detail="Parent task not found")
    if parent.user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Not your task")

    if parent.status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Parent task must be completed before refining (current: {parent.status})",
        )
    if not parent.state_path or not os.path.exists(parent.state_path):
        raise HTTPException(
            status_code=400,
            detail="Parent task's latent state is missing and cannot be refined.",
        )

    params = json.loads(parameters) if parameters else {}
    refine_params = validate_refine_parameters(params, preset_key=preset)

    # Create the refine task. It shares the parent's input image so it shows
    # up in history with a thumbnail; parameters carry the refine knobs.
    refine_task = Task(
        user_id=user.id,
        status="queued",
        input_image_path=parent.input_image_path,
        parameters=refine_params,
        parent_task_id=parent.id,
        refine_preset=preset,
        refine_status="queued",
        state_path=parent.state_path,
        camera_angle_x=parent.camera_angle_x,
        camera_distance=parent.camera_distance,
    )
    db.add(refine_task)
    await db.commit()
    await db.refresh(refine_task)

    await task_queue.submit(refine_task.id, user.id)
    return refine_task


@router.post("/cleanup")
async def cleanup_tasks(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin-only: delete all failed and cancelled tasks.

    Also converts tasks stuck in "cancelling" for more than 5 minutes to
    "failed" before deleting them.
    """
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin privileges required")

    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select, update as sa_update

    # 1. Find tasks stuck in "cancelling" for > 5 minutes
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    result = await db.execute(
        select(Task).where(
            Task.status == "cancelling",
            (Task.started_at != None) & (Task.started_at < cutoff),
        )
    )
    stuck_tasks = result.scalars().all()
    for t in stuck_tasks:
        t.status = "failed"
        t.error_message = "Task was stuck in cancelling state (timeout > 5 min)"
        t.completed_at = datetime.now(timezone.utc)
    if stuck_tasks:
        await db.commit()

    # 2. Collect all failed and cancelled tasks
    result = await db.execute(
        select(Task).where(Task.status.in_(["failed", "cancelled"]))
    )
    tasks_to_delete = result.scalars().all()
    deleted_count = len(tasks_to_delete)

    # 3. Delete associated files
    for task in tasks_to_delete:
        if task.output_glb_path:
            glb_path = settings.OUTPUT_DIR / task.output_glb_path
            if glb_path.exists():
                try:
                    os.remove(glb_path)
                except Exception:
                    pass
        if task.output_glb_path_refined:
            refined_path = settings.OUTPUT_DIR / task.output_glb_path_refined
            if refined_path.exists():
                try:
                    os.remove(refined_path)
                except Exception:
                    pass
        render_dir = settings.RENDERS_DIR / f"task_{task.id}"
        if render_dir.exists():
            try:
                shutil.rmtree(render_dir)
            except Exception:
                pass

    # 4. Delete from DB
    for task in tasks_to_delete:
        await db.delete(task)
    await db.commit()

    return {
        "detail": f"Cleaned up {deleted_count} task(s)",
        "deleted_count": deleted_count,
        "stuck_cancelling_fixed": len(stuck_tasks),
    }


@router.patch("/{task_id}/rating", response_model=TaskResponse)
async def update_rating(
    task_id: int,
    payload: RatingUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalars().first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Not your task")
    task.rating = payload.rating
    await db.commit()
    await db.refresh(task)
    return task
