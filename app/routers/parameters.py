from fastapi import APIRouter, Depends
from app.parameters import PARAMETER_DEFINITIONS

router = APIRouter(prefix="/api/parameters", tags=["parameters"])


@router.get("")
async def get_parameters():
    return PARAMETER_DEFINITIONS
