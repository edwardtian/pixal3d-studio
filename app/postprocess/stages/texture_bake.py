"""Stage 5: Texture bake — PBR (base/metal/rough/alpha) + normal + AO.

For each texel covered by the low-poly in UV space:
  - interpolate the low-poly surface position
  - find the closest point on the CLEANED high-poly via the BVH built in
    Stage 1 (the high-poly was repaired first, so this no longer bakes
    spikes/cracks/seams into the textures)
  - sample the volumetric PBR attrs at that world position via trilinear
    grid_sample_3d  (same approach as ``extract_glb``)
  - compute a tangent-space normal from the high-poly face normal vs the
    low-poly tangent basis
  - compute ambient occlusion by ray-marching against the high-poly BVH
"""

import math
import numpy as np
import torch


def _progress(ctx, step: int, total: int = 15):
    if ctx.progress is not None:
        ctx.progress.update_subtask(5, "Texture Bake (PBR+N+AO)", step, total)


def run(ctx):
    import nvdiffrast.torch as dr
    from flex_gemm.ops.grid_sample import grid_sample_3d
    from app.postprocess.stages.pbr_finalize import _compute_mikktspace_tangents

    H = W = int(ctx.texture_size)
    bake_normal = bool(ctx.params.get("refine_bake_normal", True))
    bake_ao = bool(ctx.params.get("refine_bake_ao", True))
    ao_samples = int(ctx.params.get("refine_ao_samples", 64))

    v = torch.from_numpy(ctx.lp_vertices).to("cuda")
    f = torch.from_numpy(ctx.lp_faces).to("cuda")
    uvs = torch.from_numpy(ctx.lp_uvs).to("cuda")
    _progress(ctx, 0)

    # ---- compute MikkTSpace tangents (from UVs) BEFORE baking so the bake
    # uses the same tangent basis the engine will use at render time ----
    if ctx.lp_tangents is None:
        ctx.lp_tangents = _compute_mikktspace_tangents(
            ctx.lp_vertices, ctx.lp_faces, ctx.lp_uvs, ctx.lp_normals,
        )
    tangents = torch.from_numpy(ctx.lp_tangents).to("cuda")  # (V, 4)
    _progress(ctx, 1)

    # ---- rasterize UVs ----
    ctx_obj = dr.RasterizeCudaContext()
    uvs_rast = torch.cat(
        [uvs * 2 - 1, torch.zeros_like(uvs[:, :1]), torch.ones_like(uvs[:, :1])],
        dim=-1,
    ).unsqueeze(0)
    rast = torch.zeros((1, H, W, 4), device="cuda", dtype=torch.float32)
    for i in range(0, f.shape[0], 100000):
        rast_chunk, _ = dr.rasterize(ctx_obj, uvs_rast, f[i:i + 100000],
                                     resolution=[H, W])
        mask_chunk = rast_chunk[..., 3:4] > 0
        rast_chunk[..., 3:4] += i
        rast = torch.where(mask_chunk, rast_chunk, rast)
    mask = rast[0, ..., 3] > 0
    ctx.tex_coverage = mask.cpu().numpy()
    _progress(ctx, 2)
    ctx.check_cancel()

    # ---- interpolate low-poly surface position + smooth normals + tangents ----
    pos = dr.interpolate(v.unsqueeze(0).contiguous(), rast, f)[0][0]  # (H, W, 3)
    # Interpolate per-vertex tangents (xyz) through the rasterizer.
    # NOTE: ``tangents[:, :3]`` is a strided view — nvdiffrast requires
    # contiguous inputs, so call .contiguous() explicitly.
    tan_xyz = tangents[:, :3].contiguous().unsqueeze(0)
    tan_interp = dr.interpolate(tan_xyz, rast, f)[0][0]            # (H, W, 3)
    # Interpolate the low-poly SMOOTH vertex normals (including hard-edge
    # splits) — the same normal the engine interpolates at render time. Using
    # flat face normals here would double-shade the baked normal map.
    if ctx.lp_normals is not None:
        lp_n = torch.from_numpy(np.ascontiguousarray(ctx.lp_normals)).to("cuda").float()
        lp_normals_tex = dr.interpolate(lp_n.unsqueeze(0).contiguous(), rast, f)[0][0]
        lp_normals_tex = torch.nn.functional.normalize(lp_normals_tex, dim=-1, eps=1e-6)
    else:
        lp_face_normals = _compute_face_normals(v, f)
        face_ids = (rast[0, ..., 3] - 1).long()
        face_ids = torch.clamp(face_ids, 0, f.shape[0] - 1)
        lp_normals_tex = lp_face_normals[face_ids]                  # (H, W, 3)
    _progress(ctx, 4)
    ctx.check_cancel()

    # ---- query cleaned high-poly BVH for closest point ----
    valid_pos = pos[mask]
    _, face_id, uvw = ctx.bvh.unsigned_distance(valid_pos, return_uvw=True)
    hp_v = torch.from_numpy(ctx.hp_vertices).to("cuda")
    hp_f = torch.from_numpy(ctx.hp_faces).to("cuda")
    hp_tri = hp_v[hp_f[face_id.long()]]
    closest_pos = (hp_tri * uvw.unsqueeze(-1)).sum(dim=1)
    # Interpolate the high-poly's SMOOTH vertex normals (computed in Stage 1)
    # at the closest point. Baking flat face normals reintroduces faceting
    # into the normal map on smooth surfaces.
    hp_normals_at = _interpolate_highpoly_normals(ctx.hp_normals, hp_f, face_id, uvw)
    _progress(ctx, 6)
    ctx.check_cancel()

    # ---- sample volumetric PBR attrs at closest point ----
    aabb = ctx.aabb
    voxel_size = ctx.voxel_size
    attrs = torch.zeros(H, W, ctx.attrs.shape[1], device="cuda")
    grid_pos = ((closest_pos - aabb[0]) / voxel_size).reshape(1, -1, 3)
    sampled = grid_sample_3d(
        ctx.attrs,
        torch.cat([torch.zeros_like(ctx.coords[:, :1]), ctx.coords], dim=-1),
        shape=torch.Size([1, ctx.attrs.shape[1], ctx.res, ctx.res, ctx.res]),
        grid=grid_pos,
        mode="trilinear",
    )
    # Normalize grid_sample_3d output to (M, C) for masked assignment.
    # The function may return (1, C, M), (C, M), or other shapes.
    M = valid_pos.shape[0]
    C = ctx.attrs.shape[1]
    if sampled.shape != (M, C):
        if sampled.numel() == M * C:
            sampled = sampled.reshape(M, C)
        elif sampled.dim() == 3 and sampled.shape[0] == H and sampled.shape[1] == W:
            sampled = sampled[mask]
        else:
            sampled = sampled.reshape(M, C)
    attrs[mask] = sampled
    _progress(ctx, 9)
    ctx.check_cancel()

    # ---- split PBR channels ----
    # NOTE: ``layout["base_color"]`` is a channel-index list (e.g. [0,1,2]), so
    # ``attrs[..., base_idx]`` yields the FULL (H, W, 3) tensor (uncovered texels
    # are 0 because ``attrs`` was zero-initialized). We clip it directly — the
    # inpaint stage fills the uncovered zeros.
    layout = ctx.attr_layout
    base_idx = layout["base_color"]
    metal_idx = layout["metallic"]
    rough_idx = layout["roughness"]
    alpha_idx = layout["alpha"]

    ctx.tex_base_color = _to_uint8(attrs[..., base_idx])
    ctx.tex_metallic = _to_uint8(attrs[..., metal_idx])
    ctx.tex_roughness = _to_uint8(attrs[..., rough_idx])
    ctx.tex_alpha = _to_uint8(attrs[..., alpha_idx])
    _progress(ctx, 10)

    # ---- bake tangent-space normal map ----
    if bake_normal:
        yflip = str(ctx.params.get("refine_normal_yflip", "directx"))
        ctx.tex_normal = _bake_normal_map(
            mask, lp_normals_tex, tan_interp, hp_normals_at, valid_pos,
            closest_pos, v, f, rast, H, W, yflip,
        )
        _progress(ctx, 12)
        ctx.check_cancel()
    else:
        # Neutral normal (0,0,1) in tangent space.
        ctx.tex_normal = np.zeros((H, W, 3), dtype=np.uint8)
        ctx.tex_normal[..., 2] = 255

    # ---- bake AO ----
    if bake_ao:
        ctx.tex_ao = _bake_cavity_ao(
            mask, lp_normals_tex, hp_normals_at, valid_pos, ctx.bvh,
            H, W, ao_samples,
        )
        _progress(ctx, 14)
        ctx.check_cancel()
    else:
        ctx.tex_ao = np.full((H, W), 255, dtype=np.uint8)

    _progress(ctx, 15, 15)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _compute_face_normals(v: torch.Tensor, f: torch.Tensor) -> torch.Tensor:
    a = v[f[:, 0]]
    b = v[f[:, 1]]
    c = v[f[:, 2]]
    n = torch.cross(b - a, c - a, dim=-1)
    n = torch.nn.functional.normalize(n, dim=-1)
    return n


def _interpolate_highpoly_normals(hp_normals_np, hp_f, face_id, uvw):
    """Interpolate the high-poly's smooth per-vertex normals at the closest
    point on each face using the barycentric coordinates ``uvw`` returned by
    the BVH distance query."""
    hp_n = torch.from_numpy(np.ascontiguousarray(hp_normals_np)).to("cuda")
    tri_n = hp_n[hp_f[face_id.long()]]          # (M, 3, 3)
    n = (tri_n * uvw.unsqueeze(-1)).sum(dim=1)  # (M, 3)
    return torch.nn.functional.normalize(n, dim=-1)


def _to_uint8(texels: torch.Tensor) -> np.ndarray:
    """Convert a full-size (H, W) or (H, W, C) float attribute tensor to uint8.

    Uncovered texels are already 0 (``attrs`` is zero-initialized) and are
    filled later by the inpaint stage.
    """
    out_np = texels.cpu().numpy()
    return np.clip(out_np * 255, 0, 255).astype(np.uint8)


def _bake_normal_map(mask, lp_normals_tex, tan_interp, hp_normals_at, valid_pos,
                     closest_pos, v, f, rast, H, W, yflip="directx") -> np.ndarray:
    """Compute tangent-space normals using the MikkTSpace tangent basis.

    The tangent (T) is interpolated from per-vertex MikkTSpace tangents. The
    bitangent (B) = sign * cross(N, T). The high-poly smooth normal is
    transformed into the (T, B, N) basis — the same basis the engine
    reconstructs at render time from the stored TANGENT attribute + normal.

    ``yflip`` selects the green-channel convention: "directx" (Unreal) inverts
    Y, "opengl" (Unity) leaves it as-is.
    """
    lp_n = lp_normals_tex[mask]                  # (M, 3) low-poly face normals
    hp_n = hp_normals_at                          # (M, 3) high-poly smooth normals
    tan = tan_interp[mask]                         # (M, 3) interpolated tangent xyz

    # Orthonormalize tangent against the normal (Gram-Schmidt).
    t = torch.nn.functional.normalize(tan, dim=-1)
    dot = (t * lp_n).sum(-1, keepdim=True)
    t = t - dot * lp_n
    t = torch.nn.functional.normalize(t, dim=-1)
    # Bitangent = N × T (handedness assumed +1 here; the per-vertex tangent's
    # w component handles handedness at render time, and we bake consistently).
    b = torch.linalg.cross(lp_n, t, dim=-1)

    # Transform hp_n into (T, B, N) tangent space.
    ts = torch.stack([
        (hp_n * t).sum(-1),
        (hp_n * b).sum(-1),
        (hp_n * lp_n).sum(-1),
    ], dim=-1)

    # Apply the requested green-channel (Y) convention before encoding.
    if yflip == "directx":
        ts = ts.clone()
        ts[..., 1] = -ts[..., 1]

    # Encode to [0,255] with the standard normal-map convention.
    ts = ts.clamp(-1, 1)
    ts = (ts + 1.0) * 0.5
    out = torch.zeros(H, W, 3, device="cuda")
    out[mask] = ts
    return (out.cpu().numpy() * 255).clip(0, 255).astype(np.uint8)


def _bake_cavity_ao(mask, lp_normals_tex, hp_normals_at, valid_pos, bvh,
                    H: int, W: int, samples: int) -> np.ndarray:
    """Cavity + proximity-based ambient occlusion.

    Combines two cheap signals:
      1. Cavity: where the high-poly surface normal diverges strongly from the
         low-poly face normal (concave regions), AO is darker.
      2. Proximity: sample a few points offset along the hemisphere around the
         high-poly normal; if the unsigned distance to the high-poly at those
         points is small, geometry is nearby and contributes occlusion.

    This avoids expensive ray-marching while still producing plausible AO for
    game assets. ``samples`` controls the number of proximity probes.
    """
    M = valid_pos.shape[0]
    lp_n = lp_normals_tex[mask]
    hp_n = hp_normals_at

    # ---- cavity term ----
    # dot < 1 means concave (high-poly faces tilt away from low-poly face).
    cav = torch.clamp((lp_n * hp_n).sum(-1), 0.0, 1.0)  # 1 = flat, 0 = 90deg
    # Map: flat -> 1.0 (no occlusion), strongly concave -> ~0.7
    cavity_ao = 0.7 + 0.3 * cav

    # ---- proximity term ----
    # Probe a handful of points above the surface along the hemisphere; if the
    # unsigned distance at a probe is small, nearby geometry occludes.
    n = hp_n
    helper = torch.tensor([0.0, 0.0, 1.0], device=n.device).expand_as(n).clone()
    flip = (n[..., 2:3].abs() > 0.999)
    helper = torch.where(flip, torch.tensor([1.0, 0.0, 0.0], device=n.device), helper)
    t = torch.nn.functional.normalize(torch.linalg.cross(n, helper, dim=-1), dim=-1)
    b = torch.nn.functional.normalize(torch.linalg.cross(n, t, dim=-1), dim=-1)

    probes = min(max(samples, 8), 64)  # cap probes for performance
    rng = np.random.default_rng(1337)
    local = _cosine_hemisphere(probes, rng)
    local = torch.from_numpy(local).to(valid_pos.device).float()

    radius = 0.08
    occ_count = torch.zeros(M, device=valid_pos.device)
    chunk = max(1, probes // 4)
    for s0 in range(0, probes, chunk):
        s1 = min(s0 + chunk, probes)
        d_local = local[s0:s1]                                   # (S, 3)
        d_world = (d_local[None, ..., 0:1] * t[:, None, :]
                   + d_local[None, ..., 1:2] * b[:, None, :]
                   + d_local[None, ..., 2:3] * n[:, None, :])    # (M, S, 3)
        probe_pos = valid_pos[:, None, :] + d_world * radius     # (M, S, 3)
        probe_pos = probe_pos.reshape(-1, 3)
        dist = bvh.unsigned_distance(probe_pos)
        # unsigned_distance may return a tuple when return_uvw=True; normalize.
        if isinstance(dist, tuple):
            dist = dist[0]
        # If the nearest surface is closer than the probe height, the probe
        # is "inside" or very near geometry -> occluded.
        occ_count += (dist.reshape(M, -1) < radius * 0.5).float().sum(dim=1)
    prox_ao = 1.0 - (occ_count / probes).clamp(0, 1) * 0.6  # max 60% darkening

    ao = (cavity_ao * prox_ao).clamp(0, 1)
    out = torch.zeros(H, W, device=valid_pos.device)
    out[mask] = ao
    return (out.cpu().numpy() * 255).clip(0, 255).astype(np.uint8)


def _cosine_hemisphere(n: int, rng: np.random.Generator) -> np.ndarray:
    """Generate ``n`` cosine-weighted hemisphere directions in Z-up local
    space. PDF = cos(theta)/pi."""
    u1 = rng.random(n)
    u2 = rng.random(n)
    r = np.sqrt(1 - u1)
    theta = 2 * np.pi * u2
    x = r * np.cos(theta)
    y = r * np.sin(theta)
    z = np.sqrt(np.maximum(u1, 0))
    return np.stack([x, y, z], axis=1).astype(np.float32)
