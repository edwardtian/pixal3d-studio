import asyncio
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.models import Task


@dataclass
class QueueEntry:
    task_id: int
    user_id: int


class TaskQueue:
    def __init__(self):
        self._queue: asyncio.Queue[QueueEntry] = asyncio.Queue()
        self._entries: list[QueueEntry] = []
        self._current: Optional[QueueEntry] = None
        self._lock = asyncio.Lock()

    async def submit(self, task_id: int, user_id: int) -> int:
        entry = QueueEntry(task_id=task_id, user_id=user_id)
        async with self._lock:
            self._entries.append(entry)
            await self._queue.put(entry)
            return len(self._entries) - (0 if self._current is None else 1)

    async def get_next(self) -> QueueEntry:
        entry = await self._queue.get()
        async with self._lock:
            self._current = entry
            if entry in self._entries:
                self._entries.remove(entry)
        return entry

    async def complete_current(self):
        async with self._lock:
            self._current = None

    async def get_position(self, task_id: int) -> int:
        async with self._lock:
            for i, entry in enumerate(self._entries):
                if entry.task_id == task_id:
                    return i + 1
            if self._current and self._current.task_id == task_id:
                return 0
            return -1

    async def get_status(self) -> dict:
        async with self._lock:
            return {
                "total_waiting": len(self._entries),
                "gpu_busy": self._current is not None,
            }

    async def get_user_position(self, user_id: int) -> Optional[int]:
        async with self._lock:
            if self._current and self._current.user_id == user_id:
                return 0
            for i, entry in enumerate(self._entries):
                if entry.user_id == user_id:
                    return i + 1
            return None


task_queue = TaskQueue()
