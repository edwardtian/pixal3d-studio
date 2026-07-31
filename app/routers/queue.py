from fastapi import APIRouter, Depends
from app.queue import task_queue
from app.auth import get_current_user
from app.models import User
from app.schemas import QueueStatus

router = APIRouter(prefix="/api/queue", tags=["queue"])


@router.get("/status", response_model=QueueStatus)
async def queue_status(user: User = Depends(get_current_user)):
    status = await task_queue.get_status()
    your_pos = await task_queue.get_user_position(user.id)
    return QueueStatus(
        total_waiting=status["total_waiting"],
        gpu_busy=status["gpu_busy"],
        your_position=your_pos,
    )
