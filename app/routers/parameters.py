from fastapi import APIRouter, Depends
from app.parameters import PARAMETER_DEFINITIONS
from app.postprocess.config import REFINE_PARAMETER_DEFINITIONS, REFINE_PRESETS

router = APIRouter(prefix="/api/parameters", tags=["parameters"])


@router.get("")
async def get_parameters():
    return PARAMETER_DEFINITIONS


@router.get("/refine")
async def get_refine_parameters():
    return {
        "parameters": REFINE_PARAMETER_DEFINITIONS,
        "presets": {k: {"label": p.label, "description": p.description,
                        "params": p.params} for k, p in REFINE_PRESETS.items()},
    }
