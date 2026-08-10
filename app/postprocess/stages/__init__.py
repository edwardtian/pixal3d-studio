"""Refine pipeline stages.

Each stage is a module exposing ``run(ctx: RefineContext)``. Stages mutate the
context in place and report progress through ``ctx.progress.update_subtask``.
"""

from app.postprocess.stages import (
    geometry_repair,
    remesh,
    retopology,
    uv_optimize,
    texture_bake,
    texture_inpaint,
    pbr_finalize,
    compression,
    validate,
)

__all__ = [
    "geometry_repair",
    "remesh",
    "retopology",
    "uv_optimize",
    "texture_bake",
    "texture_inpaint",
    "pbr_finalize",
    "compression",
    "validate",
]
