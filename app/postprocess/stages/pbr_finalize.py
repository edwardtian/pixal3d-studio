"""Stage 7: PBR finalize — tangents, alpha-mode detection, material build.

Computes MikkTSpace tangents (required by game engines to use the normal map),
inspects the alpha channel to pick the correct glTF ``alphaMode`` (OPAQUE /
MASK / BLEND), and assembles the trimesh PBR material with the baked
baseColor, metallicRoughness, normal, and occlusion textures.

The mesh is axis-transformed and rotated for export here so the compression
stage only has to call ``gltfpack`` / ``export``.
"""

import numpy as np
from PIL import Image


def _progress(ctx, step: int, total: int = 5):
    if ctx.progress is not None:
        ctx.progress.update_subtask(7, "PBR Finalize", step, total)


def run(ctx):
    import trimesh
    import trimesh.visual

    _progress(ctx, 0)

    # ---- compute MikkTSpace tangents (if not already done in texture_bake) ----
    if ctx.lp_tangents is None:
        ctx.lp_tangents = _compute_mikktspace_tangents(
            ctx.lp_vertices, ctx.lp_faces, ctx.lp_uvs, ctx.lp_normals,
        )
    _progress(ctx, 1)
    ctx.check_cancel()

    # ---- detect alpha mode from the baked alpha map ----
    ctx.alpha_mode, ctx.alpha_cutoff = _detect_alpha_mode(ctx.tex_alpha,
                                                          ctx.tex_coverage)
    print(f"[Refine/PBR] alpha_mode = {ctx.alpha_mode}", flush=True)
    _progress(ctx, 2)

    # ---- assemble textures ----
    # Single-channel textures may have an extra trailing dim (H, W, 1) depending
    # on how the attr layout indices were applied — squeeze to (H, W) first.
    tex_alpha = ctx.tex_alpha
    if tex_alpha.ndim == 3:
        tex_alpha = tex_alpha.squeeze(-1)
    tex_metallic = ctx.tex_metallic
    if tex_metallic.ndim == 3:
        tex_metallic = tex_metallic.squeeze(-1)
    tex_roughness = ctx.tex_roughness
    if tex_roughness.ndim == 3:
        tex_roughness = tex_roughness.squeeze(-1)

    base_color_img = Image.fromarray(np.concatenate(
        [ctx.tex_base_color, tex_alpha[..., None]], axis=-1))  # RGBA

    # glTF metallicRoughness packing: R=0, G=roughness, B=metallic.
    mr = np.concatenate([
        np.zeros_like(tex_metallic[..., None]),   # R unused
        tex_roughness[..., None],                 # G = roughness
        tex_metallic[..., None],                  # B = metallic
    ], axis=-1)
    mr_img = Image.fromarray(mr)

    normal_img = Image.fromarray(ctx.tex_normal)
    ao_img = Image.fromarray(ctx.tex_ao)
    _progress(ctx, 3)

    # ---- build trimesh material ----
    material = trimesh.visual.material.PBRMaterial(
        baseColorTexture=base_color_img,
        baseColorFactor=np.array([255, 255, 255, 255], dtype=np.uint8),
        metallicRoughnessTexture=mr_img,
        metallicFactor=1.0,
        roughnessFactor=1.0,
        alphaMode=ctx.alpha_mode,
        alphaCutoff=ctx.alpha_cutoff if ctx.alpha_mode == "MASK" else None,
        doubleSided=ctx.double_sided,
    )
    # glTF supports a separate occlusionTexture; trimesh PBRMaterial exposes it.
    try:
        material.occlusionTexture = ao_img
    except Exception:
        pass
    try:
        material.normalTexture = normal_img
    except Exception:
        pass

    # ---- axis transform + build trimesh (same convention as extract_glb) ----
    vertices_np = ctx.lp_vertices.copy()
    faces_np = ctx.lp_faces.copy()
    uvs_np = ctx.lp_uvs.copy()
    normals_np = ctx.lp_normals.copy() if ctx.lp_normals is not None else None

    # Y-up swap: y,z = z,-y
    vertices_np[:, 1], vertices_np[:, 2] = vertices_np[:, 2], -vertices_np[:, 1]
    if normals_np is not None:
        normals_np[:, 1], normals_np[:, 2] = normals_np[:, 2], -normals_np[:, 1]
    # glTF UV v-flip
    uvs_np[:, 1] = 1 - uvs_np[:, 1]

    glb = trimesh.Trimesh(
        vertices=vertices_np, faces=faces_np,
        vertex_normals=normals_np,
        process=False,
        visual=trimesh.visual.TextureVisuals(uv=uvs_np, material=material),
    )
    # Attach MikkTSpace tangents as a glTF TANGENT attribute (xyz + handedness).
    # Game engines require this to interpret the baked normal map.
    if ctx.lp_tangents is not None:
        tangents_np = ctx.lp_tangents.astype(np.float32)
        # Apply the same Y-up axis transform to tangent xyz.
        tangents_np[:, 1], tangents_np[:, 2] = tangents_np[:, 2], -tangents_np[:, 1]
        glb.vertex_attributes["tangent"] = tangents_np

    # Bake the export rotation: matches the generation pipeline.
    rot = np.array([[-1, 0, 0, 0],
                    [0, 0, -1, 0],
                    [0, -1, 0, 0],
                    [0, 0, 0, 1]], dtype=np.float64)
    glb.apply_transform(rot)

    ctx.glb = glb
    print(f"[Refine/PBR] material assembled: alpha={ctx.alpha_mode}, "
          f"base={ctx.tex_base_color.shape[:2]}, normal+baked, ao baked",
          flush=True)
    _progress(ctx, 5, 5)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _detect_alpha_mode(alpha: np.ndarray, coverage: np.ndarray):
    """Inspect the alpha channel to pick the glTF alpha mode.

    - If alpha is all 255 (opaque) -> OPAQUE.
    - If alpha has only 0/255 values -> MASK (alpha test).
    - If alpha has a continuous range -> BLEND.
    Returns (mode, cutoff).
    """
    if alpha is None:
        return "OPAQUE", 0.5
    a = alpha.astype(np.int32)
    # Only consider covered texels.
    a = a[coverage]
    if a.size == 0:
        return "OPAQUE", 0.5
    mn, mx = int(a.min()), int(a.max())
    if mn >= 250:
        return "OPAQUE", 0.5
    # If a meaningful fraction is fully transparent, treat as BLEND.
    fully_transparent = float((a < 8).sum()) / a.size
    partially_transparent = float(((a > 8) & (a < 248)).sum()) / a.size
    if partially_transparent > 0.02 or fully_transparent > 0.05:
        return "BLEND", 0.5
    return "MASK", 0.5


def _compute_mikktspace_tangents(vertices, faces, uvs, normals):
    """Compute tangent-space tangents (xyz + handedness, per vertex).

    Uses the standard tangent computation from UV derivatives (the same math
    MikkTSpace uses, without the split-normal refinement). Returns (V, 4).
    """
    v = vertices.astype(np.float64)
    f = faces.astype(np.int64)
    t = uvs.astype(np.float64)

    v0, v1, v2 = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    uv0, uv1, uv2 = t[f[:, 0]], t[f[:, 1]], t[f[:, 2]]

    dp1 = v1 - v0
    dp2 = v2 - v0
    du1 = uv1 - uv0
    du2 = uv2 - uv0

    det = du1[:, 0] * du2[:, 1] - du2[:, 0] * du1[:, 1]
    # Avoid divide-by-zero; for degenerate UVs fall back to (1,0,0,1).
    safe = np.abs(det) > 1e-12
    inv = np.zeros_like(det)
    inv[safe] = 1.0 / det[safe]

    t_dir = (dp1 * (du2[:, 1] * inv)[:, None] - dp2 * (du1[:, 1] * inv)[:, None])
    b_dir = (dp2 * (du1[:, 0] * inv)[:, None] - dp1 * (du2[:, 0] * inv)[:, None])

    # Accumulate tangents/bitangents per vertex.
    tan = np.zeros_like(v)
    bit = np.zeros_like(v)
    for i in range(3):
        np.add.at(tan, f[:, i], t_dir)
        np.add.at(bit, f[:, i], b_dir)

    # Orthonormalize against the vertex normal.
    n = normals.astype(np.float64)
    tan = _normalize(tan)
    # Gram-Schmidt
    dot = (tan * n).sum(-1, keepdims=True)
    tan = tan - dot * n
    tan = _normalize(tan)
    # Handedness: sign of dot(cross(n, tan), bit)
    handed = np.sign((np.cross(n, tan) * bit).sum(-1))
    handed[handed == 0] = 1.0

    return np.concatenate([tan.astype(np.float32), handed[:, None].astype(np.float32)],
                          axis=-1)


def _normalize(x):
    norm = np.linalg.norm(x, axis=-1, keepdims=True)
    norm = np.where(norm > 1e-12, norm, 1.0)
    return x / norm
