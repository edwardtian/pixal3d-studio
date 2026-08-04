import asyncio
import threading
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.models import Task


@dataclass
class QueueEntry:
    task_id: int
    user_id: int
    gpu_id: Optional[int] = None
    cancel_event: Optional[threading.Event] = None


class TaskQueue:
    def __init__(self):
        self._queue: asyncio.Queue[QueueEntry] = asyncio.Queue()
        self._entries: list[QueueEntry] = []
        self._current: dict[int, QueueEntry] = {}  # gpu_id -> entry currently being processed
        self._lock = asyncio.Lock()

    async def submit(self, task_id: int, user_id: int) -> int:
        entry = QueueEntry(task_id=task_id, user_id=user_id)
        async with self._lock:
            self._entries.append(entry)
            await self._queue.put(entry)
            return len(self._entries)

    async def get_next(self) -> QueueEntry:
        entry = await self._queue.get()
        async with self._lock:
            self._entries.remove(entry) if entry in self._entries else None
        return entry

    async def assign_to_gpu(self, entry: QueueEntry, gpu_id: int):
        async with self._lock:
            entry.gpu_id = gpu_id
            self._current[gpu_id] = entry

    async def complete_current(self, gpu_id: int):
        async with self._lock:
            self._current.pop(gpu_id, None)

    async def cancel(self, task_id: int) -> str:
        """Cancel a task. Returns 'queued' (removed from queue) or 'processing'
        (cancel flag set, worker will abort at next sub-task boundary) or
        'notfound'."""
        async with self._lock:
            # Queued?
            for i, entry in enumerate(self._entries):
                if entry.task_id == task_id:
                    del self._entries[i]
                    # Note: removing from the underlying asyncio.Queue is not
                    # possible; instead we mark it so the dispatcher skips it.
                    entry.cancel_event = threading.Event()
                    entry.cancel_event.set()
                    return "queued"
            # Currently processing?
            for gpu_id, entry in self._current.items():
                if entry.task_id == task_id:
                    if entry.cancel_event is None:
                        entry.cancel_event = threading.Event()
                    entry.cancel_event.set()
                    return "processing"
        return "notfound"

    async def is_cancelled(self, task_id: int) -> bool:
        async with self._lock:
            for entry in self._entries:
                if entry.task_id == task_id and entry.cancel_event and entry.cancel_event.is_set():
                    return True
            for entry in self._current.values():
                if entry.task_id == task_id and entry.cancel_event and entry.cancel_event.is_set():
                    return True
        return False

    async def get_cancel_event(self, task_id: int) -> Optional[threading.Event]:
        async with self._lock:
            for entry in self._current.values():
                if entry.task_id == task_id:
                    return entry.cancel_event
            for entry in self._entries:
                if entry.task_id == task_id:
                    return entry.cancel_event
        return None

    async def get_position(self, task_id: int) -> int:
        async with self._lock:
            for i, entry in enumerate(self._entries):
                if entry.task_id == task_id:
                    return i + 1
            for entry in self._current.values():
                if entry.task_id == task_id:
                    return 0
            return -1

    async def get_status(self) -> dict:
        async with self._lock:
            # Filter out cancelled-but-still-queued entries
            waiting = [e for e in self._entries if not (e.cancel_event and e.cancel_event.is_set())]
            return {
                "total_waiting": len(waiting),
                "num_busy": len(self._current),
                "current": [
                    {"task_id": e.task_id, "user_id": e.user_id, "gpu_id": gpu_id}
                    for gpu_id, e in self._current.items()
                ],
            }

    async def get_user_position(self, user_id: int) -> Optional[int]:
        async with self._lock:
            for entry in self._current.values():
                if entry.user_id == user_id:
                    return 0
            pos = 0
            for entry in self._entries:
                if entry.cancel_event and entry.cancel_event.is_set():
                    continue
                pos += 1
                if entry.user_id == user_id:
                    return pos
            return None

    async def get_active_task_ids(self) -> list[int]:
        async with self._lock:
            ids = [e.task_id for e in self._current.values()]
            ids.extend(e.task_id for e in self._entries if not (e.cancel_event and e.cancel_event.is_set()))
            return ids


task_queue = TaskQueue()
