import asyncio
import os
import shutil
import traceback
from datetime import datetime, timezone
from typing import Optional
import threading

from sqlalchemy import select, update

from app.database import async_session
from app.models import Task
from app.queue import task_queue
from app.parameters import validate_parameters
from app.pipeline import ProgressCallback, preprocess_image, generate_3d, extract_glb, CancelledError
from app.config import settings
from app.stages import SUBTASK_TOTAL, overall_progress


async def start_worker():
    """Kept for backward compatibility; the WorkerManager is started by main.py."""
    from app.gpu import worker_manager
    await worker_manager.start()


async def process_task(task_id: int, gpu_id: int, cancel_event: Optional[threading.Event] = None):
    """Process a single task on the given GPU. Called by WorkerManager."""
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalars().first()
        if task is None:
            return

        task.status = "processing"
        task.started_at = datetime.now(timezone.utc)
        task.assigned_gpu = gpu_id
        task.subtask_total = SUBTASK_TOTAL
        task.subtask_index = 0
        task.subtask_name = "Initializing"
        task.progress = "Initializing..."
        task.overall_progress = 0
        await db.commit()

        task_renders_dir = str(settings.RENDERS_DIR / f"task_{task_id}")
        os.makedirs(task_renders_dir, exist_ok=True)

        try:
            params = validate_parameters(task.parameters)

            # If the task was cancelled while queued, abort now
            if cancel_event is not None and cancel_event.is_set():
                raise CancelledError("Task cancelled")

            progress = ProgressCallback()
            progress_task = asyncio.create_task(_progress_updater(task_id, progress))

            input_image_path = str(settings.UPLOAD_DIR / task.input_image_path)
            preprocessed_path = preprocess_image(input_image_path, gpu_id=gpu_id)
            task.preprocessed_image_path = preprocessed_path
            await db.commit()

            if cancel_event is not None and cancel_event.is_set():
                progress_task.cancel()
                raise CancelledError("Task cancelled")

            gen_result = await asyncio.get_event_loop().run_in_executor(
                None,
                generate_3d,
                preprocessed_path,
                params,
                progress,
                task_renders_dir,
                gpu_id,
                cancel_event,
            )

            render_paths_rel = {}
            for mode, files in gen_result["render_paths"].items():
                render_paths_rel[mode] = [os.path.basename(f) for f in files]

            task.render_paths = render_paths_rel
            task.state_path = gen_result["state_path"]
            task.camera_angle_x = str(gen_result["camera_angle_x"])
            task.camera_distance = str(gen_result["distance"])
            task.progress = "Generation complete"
            await db.commit()

            progress_task.cancel()

            if cancel_event is not None and cancel_event.is_set():
                raise CancelledError("Task cancelled")

            glb_path = str(settings.OUTPUT_DIR / f"task_{task_id}.glb")
            progress2 = ProgressCallback()
            progress_task2 = asyncio.create_task(_progress_updater(task_id, progress2))

            await asyncio.get_event_loop().run_in_executor(
                None,
                extract_glb,
                gen_result["state_path"],
                params["decimation_target"],
                params["texture_size"],
                progress2,
                glb_path,
                gpu_id,
                cancel_event,
            )

            progress_task2.cancel()

            # Final cancel check (in case cancelled during GLB)
            if cancel_event is not None and cancel_event.is_set():
                raise CancelledError("Task cancelled")

            task.output_glb_path = os.path.basename(glb_path)
            task.status = "completed"
            task.progress = "Done"
            task.subtask_index = SUBTASK_TOTAL
            task.overall_progress = 100
            task.completed_at = datetime.now(timezone.utc)
            await db.commit()

        except CancelledError:
            traceback.print_exc()
            await _cleanup_cancelled(task_id, task_renders_dir)
            async with async_session() as db2:
                await db2.execute(
                    update(Task).where(Task.id == task_id).values(
                        status="cancelled",
                        progress="Cancelled",
                        completed_at=datetime.now(timezone.utc),
                    )
                )
                await db2.commit()

        except Exception as e:
            traceback.print_exc()
            task.status = "failed"
            task.error_message = str(e) + "\ntask.input_image_path = " + task.input_image_path
            task.progress = "Failed"
            task.completed_at = datetime.now(timezone.utc)
            await db.commit()


async def _cleanup_cancelled(task_id: int, renders_dir: str):
    """Remove partial output files for a cancelled task."""
    try:
        glb_path = settings.OUTPUT_DIR / f"task_{task_id}.glb"
        if glb_path.exists():
            os.remove(glb_path)
    except Exception:
        pass
    try:
        if os.path.isdir(renders_dir):
            shutil.rmtree(renders_dir, ignore_errors=True)
    except Exception:
        pass


async def _progress_updater(task_id: int, progress: ProgressCallback):
    while True:
        try:
            ov = overall_progress(
                progress.subtask_index,
                progress.subtask_step,
                progress.subtask_total_steps,
            )
            async with async_session() as db:
                await db.execute(
                    update(Task)
                    .where(Task.id == task_id)
                    .values(
                        progress=progress.stage or progress.subtask_name,
                        progress_step=progress.step,
                        progress_total=progress.total,
                        subtask_index=progress.subtask_index,
                        subtask_total=SUBTASK_TOTAL,
                        subtask_name=progress.subtask_name,
                        subtask_step=progress.subtask_step,
                        subtask_total_steps=progress.subtask_total_steps,
                        overall_progress=ov,
                    )
                )
                await db.commit()
        except Exception:
            pass
        await asyncio.sleep(0.5)
