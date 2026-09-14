"""Worker process entry point.

Each GPU gets its own worker process with CUDA_VISIBLE_DEVICES set to that GPU.
This completely isolates CUDA contexts, avoiding the cross-device illegal memory
access errors that occur when multiple threads share a single CUDA process.

Generation is backend-driven (see app.backends): the worker activates the
backend requested by the task, which lazily loads its models on first use and
unloads the previously active backend so VRAM stays available for the next one.
"""

import os
import sys
import threading
import traceback
from datetime import datetime, timezone


def worker_entry(gpu_id: int, task_queue, busy, current_task_id, loaded, db_url: str):
    """Main loop for a worker process.

    Args:
        gpu_id: Physical GPU id (for reporting in DB).
        task_queue: multiprocessing.Queue of task_ids (int). None = shutdown.
        busy: multiprocessing.Value('b') — set True while processing.
        current_task_id: multiprocessing.Value('i') — current task id or 0.
        loaded: multiprocessing.Value('b') — True once any backend is loaded.
        db_url: SQLAlchemy DB URL (with +aiosqlite, will be stripped for sync).
    """
    # Set CUDA_VISIBLE_DEVICES BEFORE importing torch or any app modules.
    os.environ['CUDA_VISIBLE_DEVICES'] = str(gpu_id)

    # Imports that touch torch must come after the env var is set.
    from sqlalchemy import create_engine, update, text
    from sqlalchemy.orm import sessionmaker

    from app.backends.registry import backend_registry as registry
    from app.models import Task

    # Create sync DB engine
    sync_db_url = db_url.replace('+aiosqlite', '')
    engine = create_engine(sync_db_url, echo=False, connect_args={'timeout': 30})
    Session = sessionmaker(engine)

    # Enable WAL mode for better concurrent read/write
    with engine.connect() as conn:
        conn.execute(text('PRAGMA journal_mode=WAL'))
        conn.commit()

    print(f"[Worker GPU {gpu_id}] Ready — backends load lazily on first task.", flush=True)

    while True:
        task_id = task_queue.get()  # blocks until a task is available
        if task_id is None:
            break  # shutdown signal

        busy.value = True
        current_task_id.value = task_id

        try:
            _process_task(task_id, gpu_id, Session)
        except Exception as e:
            print(f"[Worker GPU {gpu_id}] Task {task_id} crashed: {e}", flush=True)
            traceback.print_exc()
            # Ensure task is marked as failed
            try:
                with Session() as db:
                    db.execute(update(Task).where(Task.id == task_id).values(
                        status="failed",
                        error_message=str(e),
                        progress="Failed",
                        completed_at=datetime.now(timezone.utc),
                    ))
                    db.commit()
            except Exception:
                pass
        finally:
            busy.value = False
            current_task_id.value = 0

        # Report backend-loaded state to the GPU admin view.
        if registry.any_loaded():
            loaded.value = True

    print(f"[Worker GPU {gpu_id}] Shutting down.", flush=True)


def _process_task(task_id: int, gpu_id: int, Session):
    """Process a single task (sync, runs in worker process)."""
    from sqlalchemy import select, update
    from app.backends.base import CancelledError, ProgressCallback
    from app.backends.registry import backend_registry as registry
    from app.config import settings
    from app.models import Task
    from app.parameters import validate_parameters
    from app.stages import SUBTASK_REFINE_TOTAL, subtasks_for, overall_progress

    cancel_event = threading.Event()
    stop_monitor = threading.Event()

    # Progress monitor thread — writes progress to DB every 0.5s
    progress = ProgressCallback()

    # Whether this task is a refine pass / which backend it uses is determined
    # inside the try block; the monitor reads them via this shared holder.
    state = {"is_refine": False, "backend_id": "pixal3d", "subtask_total": 6}

    def progress_monitor():
        while not stop_monitor.is_set():
            try:
                ov = overall_progress(
                    progress.subtask_index,
                    progress.subtask_step,
                    progress.subtask_total_steps,
                    backend_id=state["backend_id"],
                    refine=state["is_refine"],
                )
                with Session() as db:
                    db.execute(update(Task).where(Task.id == task_id).values(
                        progress=progress.stage or progress.subtask_name,
                        progress_step=progress.step,
                        progress_total=progress.total,
                        subtask_index=progress.subtask_index,
                        subtask_total=state["subtask_total"],
                        subtask_name=progress.subtask_name,
                        subtask_step=progress.subtask_step,
                        subtask_total_steps=progress.subtask_total_steps,
                        overall_progress=ov,
                    ))
                    db.commit()
            except Exception:
                pass
            stop_monitor.wait(0.5)

    # Cancel checker thread — reads DB for cancel status
    def cancel_checker():
        while not stop_monitor.is_set():
            try:
                with Session() as db:
                    result = db.execute(
                        select(Task.status).where(Task.id == task_id)
                    )
                    status = result.scalar()
                    if status == 'cancelling':
                        cancel_event.set()
                        return
            except Exception:
                pass
            stop_monitor.wait(1.0)

    monitor_thread = threading.Thread(target=progress_monitor, daemon=True)
    cancel_thread = threading.Thread(target=cancel_checker, daemon=True)
    monitor_thread.start()
    cancel_thread.start()

    task_renders_dir = str(settings.RENDERS_DIR / f"task_{task_id}")
    os.makedirs(task_renders_dir, exist_ok=True)

    try:
        with Session() as db:
            task = db.execute(
                select(Task).where(Task.id == task_id)
            ).scalar_one_or_none()
            if task is None:
                return

            # Skip if the task was cancelled while waiting in the queue
            if task.status in ("cancelled", "cancelling"):
                print(f"[Worker GPU {gpu_id}] Task {task_id} was already cancelled, skipping.", flush=True)
                return

            # Dispatch: a task with a parent_task_id is a refine pass.
            is_refine = task.parent_task_id is not None
            state["is_refine"] = is_refine

            params = None
            backend_id = "pixal3d"
            if not is_refine:
                params = validate_parameters(task.parameters)
                backend_id = params["backend"]
                state["backend_id"] = backend_id
                state["subtask_total"] = len(subtasks_for(backend_id))

            task.status = "processing"
            task.started_at = datetime.now(timezone.utc)
            task.assigned_gpu = gpu_id
            if is_refine:
                task.subtask_total = SUBTASK_REFINE_TOTAL
                state["subtask_total"] = SUBTASK_REFINE_TOTAL
                task.refine_status = "processing"
            else:
                task.subtask_total = state["subtask_total"]
            task.subtask_index = 0
            task.subtask_name = "Initializing"
            task.progress = "Initializing..."
            task.overall_progress = 0
            db.commit()

            input_image_path = str(settings.UPLOAD_DIR / task.input_image_path)

        if is_refine:
            _process_refine(task_id, gpu_id, Session, progress, cancel_event,
                            stop_monitor)
            return

        if cancel_event.is_set():
            raise CancelledError("Task cancelled")

        # ---- Activate the requested backend (lazy load on first use) ----
        backend = registry.get(backend_id)
        if not backend.is_loaded():
            with Session() as db:
                db.execute(update(Task).where(Task.id == task_id).values(
                    progress=f"Loading {backend.DISPLAY_NAME} model...",
                    subtask_index=0,
                    subtask_name=f"Loading {backend.DISPLAY_NAME} model",
                ))
                db.commit()
            print(f"[Worker GPU {gpu_id}] Loading backend '{backend_id}'...", flush=True)
            registry.activate(backend_id)
            print(f"[Worker GPU {gpu_id}] Backend '{backend_id}' loaded.", flush=True)

        if cancel_event.is_set():
            raise CancelledError("Task cancelled")

        # ---- Run the backend pipeline (image -> GLB) ----
        output_glb_path = str(settings.OUTPUT_DIR / f"task_{task_id}.glb")
        result = backend.run(
            input_image_path,
            params,
            progress,
            task_renders_dir,
            output_glb_path,
            cancel_event,
        )

        if cancel_event.is_set():
            raise CancelledError("Task cancelled")

        render_paths_rel = {}
        for mode, files in result.get("render_paths", {}).items():
            render_paths_rel[mode] = [os.path.basename(f) for f in files]

        with Session() as db:
            task = db.execute(
                select(Task).where(Task.id == task_id)
            ).scalar_one_or_none()
            if task:
                task.render_paths = render_paths_rel
                task.state_path = result.get("state_path") or ""
                task.preprocessed_image_path = result.get("preprocessed_image_path") or ""
                task.camera_angle_x = str(result.get("camera_angle_x") or "")
                task.camera_distance = str(result.get("distance") or "")
                task.output_glb_path = os.path.basename(output_glb_path)
                task.status = "completed"
                task.progress = "Done"
                task.subtask_index = state["subtask_total"]
                task.overall_progress = 100
                task.completed_at = datetime.now(timezone.utc)
                db.commit()

    except CancelledError:
        print(f"[Worker GPU {gpu_id}] Task {task_id} cancelled.", flush=True)
        _cleanup_cancelled(task_id, task_renders_dir)
        with Session() as db:
            db.execute(update(Task).where(Task.id == task_id).values(
                status="cancelled",
                progress="Cancelled",
                completed_at=datetime.now(timezone.utc),
            ))
            db.commit()

    except Exception as e:
        traceback.print_exc()
        with Session() as db:
            db.execute(update(Task).where(Task.id == task_id).values(
                status="failed",
                error_message=str(e),
                progress="Failed",
                completed_at=datetime.now(timezone.utc),
            ))
            db.commit()

    finally:
        stop_monitor.set()
        monitor_thread.join(timeout=2)
        cancel_thread.join(timeout=2)
        # Free VRAM
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass


def _cleanup_cancelled(task_id: int, renders_dir: str):
    """Remove partial output files for a cancelled task."""
    try:
        from app.config import settings
        glb_path = settings.OUTPUT_DIR / f"task_{task_id}.glb"
        if glb_path.exists():
            os.remove(glb_path)
    except Exception:
        pass
    try:
        import shutil
        if os.path.isdir(renders_dir):
            shutil.rmtree(renders_dir, ignore_errors=True)
    except Exception:
        pass


def _process_refine(task_id: int, gpu_id: int, Session, progress, cancel_event,
                    stop_monitor):
    """Run the post-process refine pipeline against a parent task's saved
    latent. Skips generation stages 1-5; only runs the refine stages."""
    from sqlalchemy import select, update
    from app.backends.base import CancelledError
    from app.backends.registry import backend_registry as registry
    from app.config import settings
    from app.models import Task
    from app.postprocess import run_refine
    from app.stages import SUBTASK_REFINE_TOTAL

    try:
        # Load the refine task + its parent (for the saved latent path).
        with Session() as db:
            task = db.execute(
                select(Task).where(Task.id == task_id)
            ).scalar_one_or_none()
            if task is None:
                return
            parent_id = task.parent_task_id
            parent = db.execute(
                select(Task).where(Task.id == parent_id)
            ).scalar_one_or_none()
            if parent is None:
                raise RuntimeError(f"Parent task {parent_id} not found")
            if not parent.state_path or not os.path.exists(parent.state_path):
                raise RuntimeError(
                    f"Parent task {parent_id} latent state is missing — cannot refine. "
                    "The original generation must have completed and saved its state."
                )
            state_path = parent.state_path
            params = task.parameters or {}
            refine_preset = task.refine_preset or params.get("refine_preset", "game_engine")

        # The refine pipeline needs the Pixal3D pipeline loaded (it decodes the
        # parent's saved shape/texture latents).
        backend = registry.get("pixal3d")
        if not backend.is_loaded():
            with Session() as db:
                db.execute(update(Task).where(Task.id == task_id).values(
                    progress="Loading Pixal3D model...",
                ))
                db.commit()
            print(f"[Worker GPU {gpu_id}] Loading Pixal3D backend for refine...", flush=True)
            registry.activate("pixal3d")

        print(f"[Worker GPU {gpu_id}] Refine task {task_id} (parent={parent_id}, "
              f"preset={refine_preset})", flush=True)

        glb_path = str(settings.OUTPUT_DIR / f"task_{task_id}_refined.glb")

        result = run_refine(
            state_path=state_path,
            params={**params, "refine_preset": refine_preset},
            progress=progress,
            output_path=glb_path,
            gpu_id=gpu_id,
            cancel_event=cancel_event,
        )

        if cancel_event.is_set():
            raise CancelledError("Task cancelled")

        with Session() as db:
            t = db.execute(
                select(Task).where(Task.id == task_id)
            ).scalar_one_or_none()
            if t:
                t.output_glb_path_refined = os.path.basename(glb_path)
                t.refine_status = "completed"
                t.status = "completed"
                t.progress = "Refine complete"
                t.subtask_index = SUBTASK_REFINE_TOTAL
                t.overall_progress = 100
                t.completed_at = datetime.now(timezone.utc)
                db.commit()

    except CancelledError:
        with Session() as db:
            t = db.execute(
                select(Task).where(Task.id == task_id)
            ).scalar_one_or_none()
            if t:
                t.status = "cancelled"
                t.refine_status = "cancelled"
                t.progress = "Cancelled"
                t.completed_at = datetime.now(timezone.utc)
                db.commit()

    except Exception as e:
        traceback.print_exc()
        with Session() as db:
            t = db.execute(
                select(Task).where(Task.id == task_id)
            ).scalar_one_or_none()
            if t:
                t.status = "failed"
                t.refine_status = "failed"
                t.error_message = str(e)
                t.progress = "Refine failed"
                t.completed_at = datetime.now(timezone.utc)
                db.commit()
