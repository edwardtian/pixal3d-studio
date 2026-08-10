from pydantic import BaseModel, Field
from typing import Optional, Any
from datetime import datetime


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    email: str = ""


class UserLogin(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    role: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    username: str


class TaskCreate(BaseModel):
    parameters: dict[str, Any]


class RatingUpdate(BaseModel):
    rating: int = Field(ge=0, le=5)


class TaskResponse(BaseModel):
    id: int
    user_id: int
    username: Optional[str] = None
    status: str
    progress: str
    progress_step: int
    progress_total: int
    subtask_index: int = 0
    subtask_total: int = 6
    subtask_name: str = ""
    subtask_step: int = 0
    subtask_total_steps: int = 0
    overall_progress: int = 0
    assigned_gpu: Optional[int] = None
    rating: int
    input_image_path: str
    parameters: dict[str, Any]
    output_glb_path: str
    render_paths: Any
    camera_angle_x: str
    camera_distance: str
    error_message: str
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    # Refine pass
    parent_task_id: Optional[int] = None
    refine_preset: str = ""
    output_glb_path_refined: str = ""
    refine_status: str = ""
    refine_report: Any = {}

    class Config:
        from_attributes = True


class PresetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    parameters: dict[str, Any]


class PresetUpdate(BaseModel):
    name: Optional[str] = None
    is_default: Optional[bool] = None
    is_public: Optional[bool] = None


class PresetResponse(BaseModel):
    id: int
    user_id: int
    name: str
    parameters: dict[str, Any]
    is_default: bool
    is_public: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


class QueueStatus(BaseModel):
    total_waiting: int
    gpu_busy: bool
    num_busy: int = 0
    your_position: Optional[int]
    gpus: list[dict] = []
    active_tasks: list[dict] = []


class QueueTaskSummary(BaseModel):
    id: int
    user_id: int
    username: str
    status: str
    subtask_index: int = 0
    subtask_total: int = 6
    subtask_name: str = ""
    subtask_step: int = 0
    subtask_total_steps: int = 0
    overall_progress: int = 0
    assigned_gpu: Optional[int] = None
    queue_position: Optional[int] = None
    created_at: datetime


class GPUInfo(BaseModel):
    id: int
    name: str
    mem_total_mb: int
    mem_used_mb: int
    mem_free_mb: int
    utilization_pct: int
    enabled: bool


class WorkerStatus(BaseModel):
    gpu_id: int
    busy: bool
    current_task_id: Optional[int] = None
    pipeline_loaded: bool
    stop_requested: bool


class GPUsConfig(BaseModel):
    enabled_ids: list[int]


class GPUsConfigResponse(BaseModel):
    physical: list[GPUInfo]
    enabled: list[int]
    workers: list[WorkerStatus]
    min_free_vram_gb: float
    util_threshold: int
