"""Refine pipeline orchestrator.

Runs the configured sequence of refine stages on a saved latent
(``state_*.npz``), producing a refined GLB at ``output_path``. Mirrors the
``ProgressCallback`` / sub-task pattern used by the generation pipeline.
"""

from typing import Optional
import threading

from app.postprocess.context import RefineContext
from app.postprocess.config import validate_refine_parameters
from app.postprocess.stages import (
    geometry_repair,
    remesh,
    retopology,
    uv_optimize,
    texture_bake,
    texture_inpaint,
    pbr_finalize,
    compression,
    validate as validate_stage,
)
from app.postprocess.stages import quad_retopology


def _build_stages(params: dict):
    """Build the ordered stage list based on retopology mode."""
    mode = params.get("refine_retopology_mode", "triangle")
    if mode == "quad":
        # Quad retopology replaces both remesh + decimation stages
        return [
            ("Repair High-Poly Source", geometry_repair.run, 10),
            ("Quad Retopology",         quad_retopology.run, 5),
            ("UV Optimize",             uv_optimize.run,     8),
            ("Texture Bake (PBR+N+AO)", texture_bake.run,   15),
            ("Texture Inpaint",         texture_inpaint.run, 5),
            ("PBR Finalize",            pbr_finalize.run,    5),
            ("Compress & Export",       compression.run,    10),
            ("Validate",                validate_stage.run,  3),
        ]
    else:
        return [
            ("Repair High-Poly Source", geometry_repair.run, 10),
            ("Isotropic Remesh",        remesh.run,           5),
            ("Curvature Decimation",    retopology.run,       5),
            ("UV Optimize",             uv_optimize.run,      8),
            ("Texture Bake (PBR+N+AO)", texture_bake.run,    15),
            ("Texture Inpaint",         texture_inpaint.run,  5),
            ("PBR Finalize",            pbr_finalize.run,     5),
            ("Compress & Export",       compression.run,     10),
            ("Validate",                validate_stage.run,   3),
        ]


def run_refine(
    state_path: str,
    params: dict,
    progress,
    output_path: str,
    gpu_id: int = 0,
    cancel_event: Optional[threading.Event] = None,
) -> dict:
    """Run the full refine pipeline. Returns a small result dict.

    ``progress`` is an ``app.pipeline.ProgressCallback`` whose subtask_index
    is 1-based over the stages above. The caller is responsible for setting
    ``subtask_total`` on the task row to ``len(STAGES)``.
    """
    preset_key = params.get("refine_preset", "game_engine")
    refine_params = validate_refine_parameters(params, preset_key=preset_key)

    ctx = RefineContext(
        state_path=state_path,
        params=refine_params,
        output_path=output_path,
        gpu_id=gpu_id,
        cancel_event=cancel_event,
        progress=progress,
        texture_size=int(refine_params.get("texture_size", 2048)),
    )

    stages = _build_stages(refine_params)
    n_stages = len(stages)
    for idx, (name, fn, _steps) in enumerate(stages, start=1):
        ctx.check_cancel()
        if progress is not None:
            progress.update_subtask(idx, name, 0, _steps)
        print(f"[Refine] Stage {idx}/{n_stages}: {name}...", flush=True)
        fn(ctx)
        ctx.check_cancel()
        if progress is not None:
            progress.update_subtask(idx, name, _steps, _steps)

    return {
        "output_path": ctx.output_path,
        "validation": ctx.validation_report,
        "warnings": list(ctx.warnings),
        "alpha_mode": ctx.alpha_mode,
    }


class RefinePipeline:
    """Thin class wrapper around :func:`run_refine` for symmetry with the
    generation pipeline. Use ``run_refine`` directly in most cases."""

    def __init__(self, state_path: str, params: dict, progress, output_path: str,
                 gpu_id: int = 0, cancel_event: Optional[threading.Event] = None):
        self.state_path = state_path
        self.params = params
        self.progress = progress
        self.output_path = output_path
        self.gpu_id = gpu_id
        self.cancel_event = cancel_event

    def run(self) -> dict:
        return run_refine(
            self.state_path, self.params, self.progress, self.output_path,
            self.gpu_id, self.cancel_event,
        )
