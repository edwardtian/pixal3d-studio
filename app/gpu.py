"""GPU monitoring (pynvml) and multi-worker management.

One worker coroutine per enabled GPU. Each worker lazily loads its own pipeline
instance onto its assigned GPU. A central dispatcher pulls tasks from the shared
`task_queue` and hands them to the first idle worker whose GPU has enough free
VRAM (and is below the utilization threshold).
"""

import asyncio
import threading
import traceback
from typing import Optional

from app.config import settings
from app.queue import task_queue, QueueEntry


# ----- pynvml (optional; degrades gracefully when unavailable) -----

_nvml_initialized = False
_nvml_available = False
_nvml_lock = threading.Lock()


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
    """Return a list of all physical GPUs visible to the process.

    Each entry: {id, name, mem_total_mb, mem_used_mb, mem_free_mb, utilization_pct}.
    Falls back to torch-only enumeration (no mem/util) if pynvml is unavailable.
    """
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
            "id": i,
            "name": name,
            "mem_total_mb": 0,
            "mem_used_mb": 0,
            "mem_free_mb": 0,
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

    # 1. DB override
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

    # 2. Env default
    env = (settings.GPU_IDS or "").strip()
    if env:
        ids = [int(x) for x in env.split(",") if x.strip().isdigit()]
        if ids:
            return ids

    # 3. All visible GPUs
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


# ----- WorkerManager -----

class Worker:
    def __init__(self, gpu_id: int):
        self.gpu_id = gpu_id
        self.busy = False
        self.current_task_id: Optional[int] = None
        self.pipeline_loaded = False
        self.stop_requested = False
        self._task: Optional[asyncio.Task] = None

    def as_status(self) -> dict:
        return {
            "gpu_id": self.gpu_id,
            "busy": self.busy,
            "current_task_id": self.current_task_id,
            "pipeline_loaded": self.pipeline_loaded,
            "stop_requested": self.stop_requested,
        }


class WorkerManager:
    def __init__(self):
        self._workers: dict[int, Worker] = {}
        self._dispatcher_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._started = False

    @property
    def workers(self) -> dict[int, Worker]:
        return self._workers

    async def start(self):
        if self._started:
            return
        self._started = True
        ids = await _load_enabled_gpu_ids()
        async with self._lock:
            for gid in ids:
                if gid not in self._workers:
                    self._workers[gid] = Worker(gid)
        self._dispatcher_task = asyncio.create_task(self._dispatcher_loop())
        print(f"[WorkerManager] Started with GPUs: {list(self._workers.keys())}")

    async def reconfigure(self, new_ids: list[int]):
        """Live reconfigure: add workers for new GPUs, stop workers for removed GPUs."""
        async with self._lock:
            for gid in new_ids:
                if gid not in self._workers:
                    self._workers[gid] = Worker(gid)
                    print(f"[WorkerManager] Added worker for GPU {gid}")
            for gid in list(self._workers.keys()):
                if gid not in new_ids:
                    self._workers[gid].stop_requested = True
                    print(f"[WorkerManager] Marked worker for GPU {gid} for stop")

    async def enabled_ids(self) -> list[int]:
        async with self._lock:
            return [gid for gid, w in self._workers.items() if not w.stop_requested]

    async def get_workers_status(self) -> list[dict]:
        async with self._lock:
            return [w.as_status() for w in self._workers.values()]

    async def _dispatcher_loop(self):
        min_free_mb = int(settings.GPU_MIN_FREE_VRAM_GB * 1024)
        while True:
            try:
                await self._dispatch_once(min_free_mb)
            except Exception as e:
                print(f"[WorkerManager] Dispatcher error: {e}")
                traceback.print_exc()
            await asyncio.sleep(0.2)

    async def _dispatch_once(self, min_free_mb: int):
        # Reap stopped workers that are idle
        async with self._lock:
            for gid in list(self._workers.keys()):
                w = self._workers[gid]
                if w.stop_requested and not w.busy:
                    del self._workers[gid]
                    print(f"[WorkerManager] Removed idle worker for GPU {gid}")

        # Find an idle, non-stopping worker whose GPU is available
        candidate: Optional[Worker] = None
        async with self._lock:
            for gid, w in self._workers.items():
                if w.stop_requested or w.busy:
                    continue
                candidate = w
                break

        if candidate is None:
            return

        if not gpu_is_available(candidate.gpu_id, min_free_mb=min_free_mb):
            return

        # Try to grab a queued task
        try:
            entry = await asyncio.wait_for(task_queue.get_next(), timeout=0.05)
        except asyncio.TimeoutError:
            return

        # Skip cancelled entries
        if entry.cancel_event and entry.cancel_event.is_set():
            return

        await task_queue.assign_to_gpu(entry, candidate.gpu_id)
        candidate._task = asyncio.create_task(self._run_task(candidate, entry))

    async def _run_task(self, worker: Worker, entry: QueueEntry):
        from app.worker import process_task
        worker.busy = True
        worker.current_task_id = entry.task_id
        try:
            await process_task(entry.task_id, worker.gpu_id, entry.cancel_event)
            if not worker.pipeline_loaded:
                worker.pipeline_loaded = True
        except Exception as e:
            print(f"[WorkerManager] Worker GPU {worker.gpu_id} task {entry.task_id} crashed: {e}")
            traceback.print_exc()
        finally:
            worker.busy = False
            worker.current_task_id = None
            await task_queue.complete_current(worker.gpu_id)
            # Free VRAM cache between tasks to defragment
            try:
                import torch  # type: ignore
                torch.cuda.empty_cache()
            except Exception:
                pass


worker_manager = WorkerManager()
