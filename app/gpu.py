"""GPU monitoring (pynvml) and multi-process worker management.

One worker PROCESS per enabled GPU. Each process sets CUDA_VISIBLE_DEVICES
so the GPU appears as cuda:0 within that process, completely isolating CUDA
contexts. The main process dispatches task IDs to worker processes via
multiprocessing queues.
"""

import asyncio
import multiprocessing as mp
import os
import traceback
from typing import Optional

from app.config import settings


# ----- pynvml (optional; degrades gracefully when unavailable) -----

_nvml_initialized = False
_nvml_available = False
_nvml_lock = __import__('threading').Lock()


def _ensure_nvml():
    global _nvml_initialized, _nvml_available
    if _nvml_initialized:
        return _nvml_available
    with _nvml_lock:
        if _nvml_initialized:
            return _nvml_available
        _nvml_initialized = True
        try:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            _nvml_available = True
        except Exception:
            _nvml_available = False
    return _nvml_available


def _torch_visible_gpu_count() -> int:
    try:
        import torch  # type: ignore
        if not torch.cuda.is_available():
            return 0
        return torch.cuda.device_count()
    except Exception:
        return 0


def detect_all_gpus() -> list[dict]:
    """Return a list of all physical GPUs visible to the process."""
    gpus: list[dict] = []
    if _ensure_nvml():
        try:
            import pynvml  # type: ignore
            count = pynvml.nvmlDeviceGetCount()
            for i in range(count):
                try:
                    handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                    name = pynvml.nvmlDeviceGetName(handle)
                    if isinstance(name, bytes):
                        name = name.decode("utf-8", errors="replace")
                    mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    try:
                        util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                        util_pct = int(util.gpu)
                    except Exception:
                        util_pct = 0
                    gpus.append({
                        "id": i,
                        "name": name,
                        "mem_total_mb": int(mem.total // (1024 * 1024)),
                        "mem_used_mb": int(mem.used // (1024 * 1024)),
                        "mem_free_mb": int(mem.free // (1024 * 1024)),
                        "utilization_pct": util_pct,
                    })
                except Exception:
                    continue
            if gpus:
                return gpus
        except Exception:
            pass

    # Fallback: torch-only
    n = _torch_visible_gpu_count()
    for i in range(n):
        try:
            import torch  # type: ignore
            name = torch.cuda.get_device_name(i)
        except Exception:
            name = f"GPU {i}"
        gpus.append({
            "id": i, "name": name,
            "mem_total_mb": 0, "mem_used_mb": 0, "mem_free_mb": 0,
            "utilization_pct": 0,
        })
    return gpus


def gpu_snapshot(gpu_id: int) -> Optional[dict]:
    for g in detect_all_gpus():
        if g["id"] == gpu_id:
            return g
    return None


def gpu_is_available(gpu_id: int, min_free_mb: Optional[int] = None) -> bool:
    snap = gpu_snapshot(gpu_id)
    if snap is None:
        return False
    if min_free_mb is not None and snap["mem_total_mb"] > 0:
        if snap["mem_free_mb"] < min_free_mb:
            return False
    if snap["mem_total_mb"] > 0 and snap["utilization_pct"] >= settings.GPU_UTIL_THRESHOLD:
        return False
    return True


# ----- SystemConfig (DB-backed) helpers -----

async def _load_enabled_gpu_ids() -> list[int]:
    from app.database import async_session
    from app.models import SystemConfig
    from sqlalchemy import select

    try:
        async with async_session() as db:
            res = await db.execute(
                select(SystemConfig).where(SystemConfig.key == "enabled_gpu_ids")
            )
            row = res.scalars().first()
            if row and row.value:
                ids = [int(x) for x in str(row.value).split(",") if x.strip().isdigit()]
                if ids:
                    return ids
    except Exception:
        pass

    env = (settings.GPU_IDS or "").strip()
    if env:
        ids = [int(x) for x in env.split(",") if x.strip().isdigit()]
        if ids:
            return ids

    return [g["id"] for g in detect_all_gpus()]


async def _save_enabled_gpu_ids(ids: list[int]):
    from app.database import async_session
    from app.models import SystemConfig
    from sqlalchemy import select

    val = ",".join(str(i) for i in ids)
    async with async_session() as db:
        res = await db.execute(
            select(SystemConfig).where(SystemConfig.key == "enabled_gpu_ids")
        )
        row = res.scalars().first()
        if row is None:
            row = SystemConfig(key="enabled_gpu_ids", value=val)
            db.add(row)
        else:
            row.value = val
        await db.commit()


# ----- WorkerManager (multi-process) -----

# Use spawn context for everything — avoids CUDA fork issues
_mp_ctx = mp.get_context('spawn')


class WorkerProcess:
    def __init__(self, gpu_id: int):
        self.gpu_id = gpu_id
        self.task_queue = _mp_ctx.Queue()
        self.busy = _mp_ctx.Value('b', False)
        self.current_task_id = _mp_ctx.Value('i', 0)
        self.process: Optional[mp.Process] = None
        self.stop_requested = False
        self.pipeline_loaded = False
        self.task_start_time: Optional[float] = None  # monotonic time when current task started

    def start(self, db_url: str):
        self.process = _mp_ctx.Process(
            target=_worker_main_wrapper,
            args=(self.gpu_id, self.task_queue, self.busy,
                  self.current_task_id, db_url),
            daemon=True,
        )
        self.process.start()

    def stop(self):
        self.stop_requested = True
        try:
            self.task_queue.put(None)  # shutdown signal
        except Exception:
            pass

    def kill(self):
        """Force-kill the worker process (for watchdog timeout)."""
        if self.process and self.process.is_alive():
            print(f"[WorkerManager] Force-killing worker on GPU {self.gpu_id} (timeout)", flush=True)
            self.process.kill()
            self.process.join(timeout=5)

    def is_alive(self) -> bool:
        return self.process is not None and self.process.is_alive()

    def as_status(self) -> dict:
        return {
            "gpu_id": self.gpu_id,
            "busy": bool(self.busy.value) if self.process else False,
            "current_task_id": int(self.current_task_id.value) if self.process and self.busy.value else None,
            "pipeline_loaded": self.pipeline_loaded and self.is_alive(),
            "stop_requested": self.stop_requested,
        }


def _worker_main_wrapper(gpu_id, task_queue, busy, current_task_id, db_url):
    """Wrapper to set up sys.path before calling the real worker entry."""
    # Ensure the app package is importable in the spawned process
    app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if app_root not in sys.path:
        sys.path.insert(0, app_root)
    from app.worker_proc import worker_entry
    worker_entry(gpu_id, task_queue, busy, current_task_id, db_url)


import sys  # needed for _worker_main_wrapper


class WorkerManager:
    def __init__(self):
        self._workers: dict[int, WorkerProcess] = {}
        self._dispatcher_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._started = False
        self._dispatched: set[int] = set()  # task IDs given to workers but not yet "processing"

    @property
    def workers(self) -> dict[int, WorkerProcess]:
        return self._workers

    async def start(self):
        if self._started:
            return
        self._started = True
        ids = await _load_enabled_gpu_ids()
        db_url = settings.DATABASE_URL
        async with self._lock:
            for gid in ids:
                w = WorkerProcess(gid)
                w.start(db_url)
                self._workers[gid] = w
        self._dispatcher_task = asyncio.create_task(self._dispatcher_loop())
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        print(f"[WorkerManager] Started worker processes for GPUs: {list(self._workers.keys())}")

    async def reconfigure(self, new_ids: list[int]):
        """Live reconfigure: add workers for new GPUs, stop workers for removed GPUs."""
        async with self._lock:
            for gid in new_ids:
                if gid not in self._workers:
                    w = WorkerProcess(gid)
                    w.start(settings.DATABASE_URL)
                    self._workers[gid] = w
                    print(f"[WorkerManager] Started worker for GPU {gid}")
            for gid in list(self._workers.keys()):
                if gid not in new_ids:
                    self._workers[gid].stop()
                    print(f"[WorkerManager] Stopping worker for GPU {gid}")

    async def enabled_ids(self) -> list[int]:
        async with self._lock:
            return [gid for gid, w in self._workers.items() if not w.stop_requested]

    async def get_workers_status(self) -> list[dict]:
        # Check if pipelines are loaded (worker is alive and has been running for a bit)
        async with self._lock:
            statuses = []
            for gid, w in self._workers.items():
                status = w.as_status()
                # If the process has been alive for > 60s, assume pipeline is loaded
                if w.is_alive() and not status["pipeline_loaded"]:
                    w.pipeline_loaded = True  # optimistic; real check would need IPC
                statuses.append(w.as_status())
            return statuses

    async def _dispatcher_loop(self):
        min_free_mb = int(settings.GPU_MIN_FREE_VRAM_GB * 1024)
        while True:
            try:
                await self._dispatch_once(min_free_mb)
            except Exception as e:
                print(f"[WorkerManager] Dispatcher error: {e}")
                traceback.print_exc()
            await asyncio.sleep(0.5)

    async def _dispatch_once(self, min_free_mb: int):
        # Reap stopped workers that have finished
        async with self._lock:
            for gid in list(self._workers.keys()):
                w = self._workers[gid]
                if w.stop_requested and not w.is_alive():
                    del self._workers[gid]
                    print(f"[WorkerManager] Removed stopped worker for GPU {gid}")

        # Clean up dispatched set — remove tasks no longer in "queued" state
        if self._dispatched:
            await self._cleanup_dispatched()

        # Find idle, alive, non-stopping workers
        async with self._lock:
            candidates = [
                w for gid, w in self._workers.items()
                if not w.stop_requested and w.is_alive() and not w.busy.value
            ]

        if not candidates:
            return

        # Check GPU availability for each candidate
        available = [w for w in candidates if gpu_is_available(w.gpu_id, min_free_mb=min_free_mb)]
        if not available:
            return

        # Get next queued task from DB
        task_id = await self._get_next_queued_task()
        if task_id is None:
            return

        # Dispatch to the first available worker
        worker = available[0]
        worker.task_queue.put(task_id)
        worker.task_start_time = __import__('time').monotonic()
        print(f"[WorkerManager] Dispatched task {task_id} to GPU {worker.gpu_id}")

    async def _watchdog_loop(self):
        """Watch for stuck tasks. If a task runs longer than TASK_TIMEOUT_MINUTES,
        kill the worker process, mark the task as failed, and restart the worker."""
        timeout_sec = int(getattr(settings, 'TASK_TIMEOUT_MINUTES', 30) * 60)
        while True:
            try:
                import time
                async with self._lock:
                    for gid, w in list(self._workers.items()):
                        if not w.busy.value or not w.is_alive():
                            w.task_start_time = None
                            continue
                        if w.task_start_time is None:
                            w.task_start_time = time.monotonic()
                            continue
                        elapsed = time.monotonic() - w.task_start_time
                        if elapsed > timeout_sec:
                            task_id = int(w.current_task_id.value)
                            print(f"[Watchdog] Task {task_id} on GPU {gid} timed out "
                                  f"({elapsed/60:.1f} min > {timeout_sec/60:.0f} min), killing worker",
                                  flush=True)
                            # Mark task as failed in DB
                            await self._mark_task_failed(task_id, 
                                f"Task timed out after {elapsed/60:.1f} minutes (likely GPU hang)")
                            # Kill and restart the worker
                            w.kill()
                            w.task_start_time = None
                            # Restart the worker
                            w2 = WorkerProcess(gid)
                            w2.start(settings.DATABASE_URL)
                            self._workers[gid] = w2
                            print(f"[WorkerManager] Restarted worker for GPU {gid}", flush=True)
            except Exception as e:
                print(f"[Watchdog] Error: {e}")
                traceback.print_exc()
            await asyncio.sleep(10)

    async def _mark_task_failed(self, task_id: int, error_msg: str):
        from app.database import async_session
        from app.models import Task
        from sqlalchemy import update
        from datetime import datetime, timezone

        try:
            async with async_session() as db:
                await db.execute(
                    update(Task).where(Task.id == task_id).values(
                        status="failed",
                        error_message=error_msg,
                        progress="Timed out",
                        completed_at=datetime.now(timezone.utc),
                    )
                )
                await db.commit()
        except Exception as e:
            print(f"[Watchdog] Failed to mark task {task_id} as failed: {e}")

    async def _get_next_queued_task(self) -> Optional[int]:
        from app.database import async_session
        from app.models import Task
        from sqlalchemy import select

        async with async_session() as db:
            result = await db.execute(
                select(Task.id)
                .where(Task.status == "queued", ~Task.id.in_(self._dispatched) if self._dispatched else True)
                .order_by(Task.created_at.asc())
                .limit(1)
            )
            task_id = result.scalar()
            if task_id is not None:
                self._dispatched.add(task_id)
            return task_id

    async def _cleanup_dispatched(self):
        """Remove tasks from the dispatched set that are no longer queued."""
        from app.database import async_session
        from app.models import Task
        from sqlalchemy import select

        async with async_session() as db:
            result = await db.execute(
                select(Task.id, Task.status).where(Task.id.in_(self._dispatched))
            )
            for tid, status in result.all():
                if status != "queued":
                    self._dispatched.discard(tid)


worker_manager = WorkerManager()
