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
from app.schemas import TaskResponse
from app.auth import get_current_user
from app.config import settings
from app.parameters import validate_parameters
from app.queue import task_queue
from app.worker import start_worker

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

    await start_worker()
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

    query = select(Task)

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
    return result.scalars().all()


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

    if task.output_glb_path:
        glb_path = settings.OUTPUT_DIR / task.output_glb_path
        if glb_path.exists():
            os.remove(glb_path)

    render_dir = settings.RENDERS_DIR / f"task_{task_id}"
    if render_dir.exists():
        shutil.rmtree(render_dir)

    await db.delete(task)
    await db.commit()
    return {"detail": "Task deleted"}
