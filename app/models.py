from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, Boolean, JSON
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from datetime import datetime, timezone

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, index=True, nullable=False)
    email = Column(String(128), default="")
    hashed_password = Column(String(256), nullable=False)
    role = Column(String(16), default="user", nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    tasks = relationship("Task", back_populates="user", cascade="all, delete-orphan")
    presets = relationship("Preset", back_populates="user", cascade="all, delete-orphan")


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String(32), default="queued", nullable=False)
    progress = Column(String(256), default="")
    progress_step = Column(Integer, default=0)
    progress_total = Column(Integer, default=0)

    # Sub-task / overall progress tracking
    subtask_index = Column(Integer, default=0)
    subtask_total = Column(Integer, default=6)
    subtask_name = Column(String(128), default="")
    subtask_step = Column(Integer, default=0)
    subtask_total_steps = Column(Integer, default=0)
    overall_progress = Column(Integer, default=0)
    assigned_gpu = Column(Integer, nullable=True)

    rating = Column(Integer, default=0)

    input_image_path = Column(String(512), nullable=False)
    preprocessed_image_path = Column(String(512), default="")
    parameters = Column(JSON, default=dict)

    output_glb_path = Column(String(512), default="")
    render_paths = Column(JSON, default=dict)
    state_path = Column(String(512), default="")
    camera_angle_x = Column(String(64), default="")
    camera_distance = Column(String(64), default="")

    error_message = Column(Text, default="")

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    # ---- Refine pass ----
    # If set, this task is a refine pass re-running GLB extraction from the
    # parent's saved latent (state_path), not a fresh generation.
    parent_task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True)
    refine_preset = Column(String(64), default="")
    # Output of the refine pass (separate from output_glb_path which holds the
    # original generation's GLB for the parent task).
    output_glb_path_refined = Column(String(512), default="")
    refine_status = Column(String(32), default="")  # idle|queued|processing|completed|failed
    refine_report = Column(JSON, default=dict)       # validation + warnings

    user = relationship("User", back_populates="tasks")


class SystemConfig(Base):
    __tablename__ = "system_config"

    key = Column(String(64), primary_key=True)
    value = Column(Text, default="")


class Preset(Base):
    __tablename__ = "presets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(128), nullable=False)
    parameters = Column(JSON, default=dict)
    is_default = Column(Boolean, default=False)
    is_public = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="presets")
