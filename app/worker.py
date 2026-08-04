"""Worker module — kept for backward compatibility.

The actual task processing now happens in separate worker processes
(app.worker_proc), managed by app.gpu.WorkerManager.
"""


async def start_worker():
    """Kept for backward compatibility; the WorkerManager is started by main.py."""
    from app.gpu import worker_manager
    await worker_manager.start()
