from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import settings
from app.database import init_db
from app.gpu import worker_manager
from app.routers import auth, users, tasks, parameters, queue, presets, system


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await worker_manager.start()
    yield


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(tasks.router)
app.include_router(parameters.router)
app.include_router(queue.router)
app.include_router(presets.router)
app.include_router(system.router)

static_dir = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def index():
    return FileResponse(str(static_dir / "index.html"))


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    candidate = static_dir / full_path
    if candidate.is_file():
        return FileResponse(str(candidate))
    return FileResponse(str(static_dir / "index.html"))
