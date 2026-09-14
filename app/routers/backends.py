from fastapi import APIRouter, HTTPException

from app.backends.registry import default_backend_id, list_backends
from app.parameters import parameter_definitions_for

router = APIRouter(prefix="/api/backends", tags=["backends"])


@router.get("")
async def get_backends():
    """List all generation backends with their capabilities and status.

    Mirrors modly's /model/all: the UI renders the backend selector from this
    and fetches each backend's parameter schema from /{backend_id}/parameters.
    """
    return {"backends": list_backends(), "default": default_backend_id()}


@router.get("/{backend_id}/parameters")
async def get_backend_parameters(backend_id: str):
    """Parameter schema for one backend (used to re-render the UI form)."""
    try:
        return parameter_definitions_for(backend_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Unknown backend: {backend_id}")
