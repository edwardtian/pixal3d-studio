"""Post-process pipeline for refining generated meshes for production use.

The pipeline is composed of discrete, composable stages that operate on a
``RefineContext``. Each stage reads/writes fields on the context and reports
progress through a ``ProgressCallback``. Stages are independently runnable so
a refine pass can be re-run from a saved ``state_*.npz`` latent without
re-running 3D generation.

Stages
------
- geometry_repair : clean the high-poly source (weld, fill, manifold, smooth)
- remesh          : isotropic remesh for uniform triangle sizes
- retopology      : curvature-aware decimation to the target triangle count
- uv_optimize     : xatlas-based atlas packing (falls back to cumesh)
- texture_bake    : bake PBR (base/metal/rough/alpha) + normal + AO maps
- texture_inpaint : patch-based gap fill (replaces cv2 INPAINT_TELEA)
- pbr_finalize    : MikkTSpace tangents, alpha-mode detection, material build
- compression     : gltfpack Draco + KTX2 + meshopt (degrades gracefully)
- validate        : pre-ship checks (manifold, watertight, UV coverage, ...)
"""

from app.postprocess.context import RefineContext
from app.postprocess.config import RefinePreset, get_refine_preset, REFINE_PRESETS
from app.postprocess.pipeline import RefinePipeline, run_refine

__all__ = [
    "RefineContext",
    "RefinePreset",
    "REFINE_PRESETS",
    "get_refine_preset",
    "RefinePipeline",
    "run_refine",
]
