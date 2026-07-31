import asyncio
import os
import shutil
import traceback
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.database import async_session
from app.models import Task, User
from app.queue import task_queue
from app.parameters import validate_parameters
from app.pipeline import ProgressCallback, preprocess_image, generate_3d, extract_glb
from app.config import settings


_worker_started = False


async def start_worker():
    global _worker_started
    if _worker_started:
        return
    _worker_started = True
    asyncio.create_task(_worker_loop())


async def _worker_loop():
    while True:
        try:
            entry = await task_queue.get_next()
            await _process_task(entry.task_id)
            await task_queue.complete_current()
        except Exception as e:
            print(f"[Worker] Fatal error: {e}")
            traceback.print_exc()
            await task_queue.complete_current()
        await asyncio.sleep(0.1)


async def _process_task(task_id: int):
    async with async_session() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalars().first()
        if task is None:
            return

        task.status = "processing"
        task.started_at = datetime.now(timezone.utc)
        task.progress = "Initializing..."
        await db.commit()

        task_renders_dir = str(settings.RENDERS_DIR / f"task_{task_id}")
        os.makedirs(task_renders_dir, exist_ok=True)

        try:
            params = validate_parameters(task.parameters)

            progress = ProgressCallback()
            progress_task = asyncio.create_task(_progress_updater(task_id, progress))

            input_image_path = str(settings.UPLOAD_DIR / task.input_image_path)
            preprocessed_path = preprocess_image(input_image_path)
            task.preprocessed_image_path = preprocessed_path
            await db.commit()

            gen_result = await asyncio.get_event_loop().run_in_executor(
                None,
                generate_3d,
                preprocessed_path,
                params,
                progress,
                task_renders_dir,
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
            )

            task.output_glb_path = os.path.basename(glb_path)
            task.status = "completed"
            task.progress = "Done"
            task.completed_at = datetime.now(timezone.utc)
            await db.commit()

            progress_task2.cancel()

        except Exception as e:
            traceback.print_exc()
            task.status = "failed"
            task.error_message = str(e) + "\ntask.input_image_path = " + task.input_image_path
            task.progress = "Failed"
            task.completed_at = datetime.now(timezone.utc)
            await db.commit()


async def _progress_updater(task_id: int, progress: ProgressCallback):
    while True:
        try:
            async with async_session() as db:
                await db.execute(
                    update(Task)
                    .where(Task.id == task_id)
                    .values(
                        progress=progress.stage,
                        progress_step=progress.step,
                        progress_total=progress.total,
                    )
                )
                await db.commit()
        except Exception:
            pass
        await asyncio.sleep(0.5)
