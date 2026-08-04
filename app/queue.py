"""Task queue — DB-backed.

Tasks live in the database with status "queued". The WorkerManager dispatcher
polls the DB and dispatches task IDs to worker processes. Cancel is done by
setting status to "cancelling" in the DB; the worker process checks for this.
"""

from typing import Optional
import threading


class TaskQueue:
    """Thin wrapper around DB-based queue operations.

    No in-memory queue is needed — tasks are persisted in the DB and the
    WorkerManager dispatcher polls for them.
    """

    async def submit(self, task_id: int, user_id: int) -> int:
        """Task is already in DB with status 'queued'. Just return queue position."""
        return await self.get_position(task_id)

    async def cancel(self, task_id: int) -> str:
        """Signal cancellation. Returns 'queued', 'processing', or 'notfound'.

        The actual status update is done by the router endpoint which calls this
        method to determine the current state.
        """
        from app.database import async_session
        from app.models import Task
        from sqlalchemy import select

        async with async_session() as db:
            result = await db.execute(
                select(Task.status).where(Task.id == task_id)
            )
            status = result.scalar()
            if status is None:
                return "notfound"
            if status == "queued":
                return "queued"
            if status in ("processing", "cancelling"):
                return "processing"
            return "notfound"

    async def get_position(self, task_id: int) -> int:
        from app.database import async_session
        from app.models import Task
        from sqlalchemy import select, func

        async with async_session() as db:
            # Check if currently processing
            result = await db.execute(
                select(Task.status).where(Task.id == task_id)
            )
            status = result.scalar()
            if status is None:
                return -1
            if status == "processing":
                return 0

            # Count queued tasks ahead of this one
            result = await db.execute(
                select(func.count(Task.id)).where(
                    Task.status == "queued",
                    Task.created_at <= (
                        select(Task.created_at).where(Task.id == task_id)
                    )
                )
            )
            return result.scalar() or 0

    async def get_status(self) -> dict:
        from app.database import async_session
        from app.models import Task
        from sqlalchemy import select, func

        async with async_session() as db:
            waiting_count = await db.scalar(
                select(func.count(Task.id)).where(Task.status == "queued")
            )
            busy_count = await db.scalar(
                select(func.count(Task.id)).where(Task.status == "processing")
            )
            current = await db.execute(
                select(Task.id, Task.user_id, Task.assigned_gpu)
                .where(Task.status == "processing")
            )
            return {
                "total_waiting": waiting_count or 0,
                "num_busy": busy_count or 0,
                "current": [
                    {"task_id": tid, "user_id": uid, "gpu_id": gid}
                    for tid, uid, gid in current.all()
                ],
            }

    async def get_user_position(self, user_id: int) -> Optional[int]:
        from app.database import async_session
        from app.models import Task
        from sqlalchemy import select, func

        async with async_session() as db:
            # Check if user has a processing task
            result = await db.execute(
                select(Task.id).where(
                    Task.user_id == user_id, Task.status == "processing"
                ).limit(1)
            )
            if result.scalar() is not None:
                return 0

            # Find user's oldest queued task and count tasks ahead
            result = await db.execute(
                select(Task.created_at).where(
                    Task.user_id == user_id, Task.status == "queued"
                ).order_by(Task.created_at.asc()).limit(1)
            )
            oldest = result.scalar()
            if oldest is None:
                return None

            count = await db.scalar(
                select(func.count(Task.id)).where(
                    Task.status == "queued", Task.created_at <= oldest
                )
            )
            return count or 0

    async def get_active_task_ids(self) -> list[int]:
        from app.database import async_session
        from app.models import Task
        from sqlalchemy import select

        async with async_session() as db:
            result = await db.execute(
                select(Task.id).where(Task.status.in_(["queued", "processing", "cancelling"]))
            )
            return list(result.scalars().all())


task_queue = TaskQueue()
