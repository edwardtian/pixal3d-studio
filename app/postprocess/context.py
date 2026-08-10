"""Mutable context object passed through the refine pipeline stages."""

from dataclasses import dataclass, field
from typing import Any, Optional
import threading
import numpy as np
import torch


@dataclass
class RefineContext:
    """Carries all intermediate state between refine stages.

    The high-poly source mesh is decoded from the saved latent, repaired in
    Stage 1, and used as the bake source in Stage 5. The low-poly target mesh
    is derived from the cleaned high-poly and is what gets exported.
    """

    # ---- inputs ----
    state_path: str
    params: dict
    output_path: str
    gpu_id: int = 0
    cancel_event: Optional[threading.Event] = None
    progress: Any = None  # app.pipeline.ProgressCallback

    # ---- latent / voxel data ----
    res: int = 0
    aabb: torch.Tensor = field(default_factory=lambda: torch.zeros((2, 3)))
    voxel_size: torch.Tensor = field(default_factory=lambda: torch.zeros(3))
    # volumetric PBR attributes from decode_latent: (N, C) on cuda
    attrs: Optional[torch.Tensor] = None
    # voxel coords matching attrs: (N, 3) on cuda
    coords: Optional[torch.Tensor] = None
    # PBR attribute layout from pipeline: {base_color, metallic, roughness, alpha}
    attr_layout: dict = field(default_factory=dict)

    # ---- high-poly source mesh (the bake source) ----
    # numpy float32 (V, 3) and int32 (F, 3)
    hp_vertices: Optional[np.ndarray] = None
    hp_faces: Optional[np.ndarray] = None
    hp_normals: Optional[np.ndarray] = None
    # cumesh.cuBVH built on the cleaned high-poly (for texture bake queries)
    bvh: Any = None
    # raw (un-cleaned) high-poly vertex attributes per-vertex, if available
    hp_vertex_attrs: Optional[torch.Tensor] = None

    # ---- low-poly target mesh (what gets exported) ----
    # numpy float32 (V, 3), int32 (F, 3), float32 (V, 3) normals
    lp_vertices: Optional[np.ndarray] = None
    lp_faces: Optional[np.ndarray] = None
    lp_normals: Optional[np.ndarray] = None
    # UV coordinates (V, 2) and vertex map from UV-unwrap (if remap happened)
    lp_uvs: Optional[np.ndarray] = None
    # MikkTSpace tangents (V, 4) — xyz + handedness
    lp_tangents: Optional[np.ndarray] = None

    # ---- baked textures (H, W, C) uint8 numpy ----
    tex_base_color: Optional[np.ndarray] = None  # RGB
    tex_metallic: Optional[np.ndarray] = None    # single channel
    tex_roughness: Optional[np.ndarray] = None   # single channel
    tex_alpha: Optional[np.ndarray] = None       # single channel
    tex_normal: Optional[np.ndarray] = None      # RGB tangent-space
    tex_ao: Optional[np.ndarray] = None          # single channel
    # coverage mask: True where a triangle covered the texel
    tex_coverage: Optional[np.ndarray] = None    # bool (H, W)
    texture_size: int = 2048

    # ---- final material / export ----
    alpha_mode: str = "OPAQUE"   # OPAQUE | MASK | BLEND
    alpha_cutoff: float = 0.5
    double_sided: bool = True
    validation_report: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    # ---- helpers ----
    def check_cancel(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            from app.pipeline import CancelledError
            raise CancelledError("Refine cancelled")

    def warn(self, msg: str):
        self.warnings.append(msg)
        print(f"[Refine] WARN: {msg}", flush=True)
