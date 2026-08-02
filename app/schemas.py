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
    status: str
    progress: str
    progress_step: int
    progress_total: int
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

    class Config:
        from_attributes = True


class PresetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    parameters: dict[str, Any]


class PresetUpdate(BaseModel):
    name: Optional[str] = None
    is_default: Optional[bool] = None


class PresetResponse(BaseModel):
    id: int
    name: str
    parameters: dict[str, Any]
    is_default: bool
    created_at: datetime

    class Config:
        from_attributes = True


class QueueStatus(BaseModel):
    total_waiting: int
    gpu_busy: bool
    your_position: Optional[int]
