from fastapi import APIRouter, HTTPException

from app.backends.registry import default_backend_id
from app.parameters import parameter_definitions_for
from app.postprocess.config import REFINE_PARAMETER_DEFINITIONS, REFINE_PRESETS

router = APIRouter(prefix="/api/parameters", tags=["parameters"])


@router.get("")
async def get_parameters(backend: str | None = None):
    """Parameter definitions for a generation backend.

    Defaults to the configured default backend; the UI switches backends via
    /api/backends and re-fetches this endpoint per backend.
    """
    try:
        return parameter_definitions_for(backend or default_backend_id())
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Unknown backend: {backend}")


@router.get("/refine")
async def get_refine_parameters():
    return {
        "parameters": REFINE_PARAMETER_DEFINITIONS,
        "presets": {k: {"label": p.label, "description": p.description,
                        "params": p.params} for k, p in REFINE_PRESETS.items()},
    }
