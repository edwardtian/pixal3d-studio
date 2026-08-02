from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.database import get_db
from app.models import User, Preset
from app.schemas import PresetCreate, PresetUpdate, PresetResponse
from app.auth import get_current_user

router = APIRouter(prefix="/api/presets", tags=["presets"])


@router.get("", response_model=list[PresetResponse])
async def list_presets(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Preset).where(Preset.user_id == user.id).order_by(Preset.created_at.desc())
    )
    return result.scalars().all()


@router.post("", response_model=PresetResponse)
async def create_preset(
    payload: PresetCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Preset).where(Preset.user_id == user.id, Preset.name == payload.name)
    )
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="Preset name already exists")

    preset = Preset(
        user_id=user.id,
        name=payload.name,
        parameters=payload.parameters,
    )
    db.add(preset)
    await db.commit()
    await db.refresh(preset)
    return preset


@router.patch("/{preset_id}", response_model=PresetResponse)
async def update_preset(
    preset_id: int,
    payload: PresetUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Preset).where(Preset.id == preset_id))
    preset = result.scalars().first()
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")
    if preset.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your preset")

    if payload.name is not None:
        preset.name = payload.name

    if payload.is_default is not None:
        if payload.is_default:
            await db.execute(
                update(Preset).where(Preset.user_id == user.id).values(is_default=False)
            )
        preset.is_default = payload.is_default

    await db.commit()
    await db.refresh(preset)
    return preset


@router.delete("/{preset_id}")
async def delete_preset(
    preset_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Preset).where(Preset.id == preset_id))
    preset = result.scalars().first()
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")
    if preset.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your preset")

    await db.delete(preset)
    await db.commit()
    return {"detail": "Preset deleted"}
