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


class TaskResponse(BaseModel):
    id: int
    status: str
    progress: str
    progress_step: int
    progress_total: int
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

    class Config:
        from_attributes = True


class QueueStatus(BaseModel):
    total_waiting: int
    gpu_busy: bool
    your_position: Optional[int]
