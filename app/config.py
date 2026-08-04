import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Pixal3D Studio"
    SECRET_KEY: str = "change-me-in-production-please-use-a-long-random-string"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    DATABASE_URL: str = "sqlite+aiosqlite:///./data/db/pixal3d.db"

    # BASE_DIR: Path = Path(__file__).resolve().parent.parent
    BASE_DIR: Path = Path("/app")
    UPLOAD_DIR: Path = BASE_DIR / "data" / "uploads"
    OUTPUT_DIR: Path = BASE_DIR / "data" / "outputs"
    RENDERS_DIR: Path = BASE_DIR / "data" / "renders"
    DB_DIR: Path = BASE_DIR / "data" / "db"

    PIXAL3D_MODEL_PATH: str = "TencentARC/Pixal3D"
    MOGE_MODEL_NAME: str = "Ruicheng/moge-2-vitl"
    LOW_VRAM: bool = False
    ATTN_BACKEND: str = "sdpa"

    # Multi-GPU worker configuration
    # Comma-separated GPU ids to enable (e.g. "0,1"). Empty = auto-detect all visible GPUs.
    GPU_IDS: str = ""
    # Minimum free VRAM (GB) required on a GPU before dispatching a task to it.
    GPU_MIN_FREE_VRAM_GB: float = 12.0
    # GPU utilization % above which a GPU is considered too busy to receive a new task.
    GPU_UTIL_THRESHOLD: int = 95

    # HuggingFace model cache — set to /app/models when running in container
    HF_HOME: str = ""

    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin123"

    PORT: int = 8000

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()

MODEL_PATH = settings.PIXAL3D_MODEL_PATH

for d in [settings.UPLOAD_DIR, settings.OUTPUT_DIR, settings.RENDERS_DIR, settings.DB_DIR]:
    d.mkdir(parents=True, exist_ok=True)
