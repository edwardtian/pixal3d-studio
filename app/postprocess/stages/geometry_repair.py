"""Stage 1: Repair the high-poly source mesh.

Decodes the saved latent into a dense mesh, then cleans it BEFORE it is used
as the normal/AO bake source. Baking from a messy source propagates spikes,
cracks, and seams into the normal map where they are expensive to fix later.

Steps:
  1. decode_latent(state_*.npz) -> mesh_decode (vertices, faces, attrs)
  2. init CuMesh
  3. weld duplicate vertices  (removes seams that would split normals)
  4. fill holes               (closes gaps that would produce false AO)
  5. repair non-manifold edges
  6. remove degenerate (zero-area) triangles
  7. remove small disconnected components
  8. unify face orientations
  9. optional Taubin denoise smoothing (feature-preserving)
 10. recompute vertex normals
 11. build BVH on the CLEANED high-poly (used by texture_bake)
"""

import numpy as np
import torch


def _progress(ctx, step: int, total: int = 10):
    if ctx.progress is not None:
        ctx.progress.update_subtask(1, "Repair High-Poly Source", step, total)


def run(ctx):
    import cumesh
    from app.pipeline import init_pipeline

    init_pipeline()
    _progress(ctx, 0)

    # ---- decode latent ----
    try:
        from pixal3d.modules.sparse import SparseTensor
    except ImportError:
        from trellis2.modules.sparse import SparseTensor

    data = np.load(ctx.state_path)
    shape_slat = SparseTensor(
        feats=torch.from_numpy(data["shape_slat_feats"]).to("cuda"),
        coords=torch.from_numpy(data["coords"]).to("cuda"),
    )
    tex_slat = shape_slat.replace(torch.from_numpy(data["tex_slat_feats"]).to("cuda"))
    res = int(data["res"])
    ctx.res = res

    # Lazily reach the pipeline singleton loaded by the worker process.
    from app import pipeline as _p
    pl = _p._pipeline
    if pl is None:  # pragma: no cover - worker always inits first
        init_pipeline()
        pl = _p._pipeline

    print(f"[Refine/Repair] decode_latent(res={res})...", flush=True)
    mesh_decode = pl.decode_latent(shape_slat, tex_slat, res)[0]
    print(f"[Refine/Repair] decoded: {len(mesh_decode.vertices)} verts, "
          f"{len(mesh_decode.faces)} faces, {mesh_decode.attrs.shape[1]} attrs",
          flush=True)

    # Stash volumetric attrs + layout for the texture_bake stage.
    ctx.attrs = mesh_decode.attrs
    ctx.coords = mesh_decode.coords
    ctx.attr_layout = dict(pl.pbr_attr_layout)

    aabb = torch.tensor([[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
                        dtype=torch.float32, device="cuda")
    ctx.aabb = aabb
    ctx.voxel_size = (aabb[1] - aabb[0]) / torch.tensor([res, res, res],
                                                        dtype=torch.float32, device="cuda")
    _progress(ctx, 1)
    ctx.check_cancel()

    # ---- init CuMesh ----
    mesh = cumesh.CuMesh()
    mesh.init(vertices=mesh_decode.vertices, faces=mesh_decode.faces)
    print(f"[Refine/Repair] init: {mesh.num_vertices} verts, {mesh.num_faces} faces",
          flush=True)
    _progress(ctx, 2)

    # ---- weld duplicate vertices ----
    # CuMesh doesn't expose a weld directly; do it via numpy then re-init.
    v, f = mesh.read()
    v_np = v.cpu().numpy() if hasattr(v, "cpu") else np.asarray(v)
    f_np = f.cpu().numpy() if hasattr(f, "cpu") else np.asarray(f)
    welded_v, welded_f = _weld_vertices(v_np, f_np, tol=1e-6)
    if len(welded_v) < len(v_np):
        print(f"[Refine/Repair] welded {len(v_np) - len(welded_v)} duplicate verts",
              flush=True)
        mesh = cumesh.CuMesh()
        mesh.init(vertices=torch.from_numpy(welded_v).to("cuda"),
                  faces=torch.from_numpy(welded_f).to("cuda"))
    _progress(ctx, 3)
    ctx.check_cancel()

    # ---- fill holes ----
    mesh.fill_holes(max_hole_perimeter=3e-2)
    _progress(ctx, 4)

    # ---- repair non-manifold + remove dup faces ----
    mesh.remove_duplicate_faces()
    mesh.repair_non_manifold_edges()
    _progress(ctx, 5)

    # ---- remove degenerate triangles ----
    v, f = mesh.read()
    v_np = v.cpu().numpy() if hasattr(v, "cpu") else np.asarray(v)
    f_np = f.cpu().numpy() if hasattr(f, "cpu") else np.asarray(f)
    f_np = _remove_degenerate_faces(v_np, f_np)
    mesh = cumesh.CuMesh()
    mesh.init(vertices=torch.from_numpy(v_np).to("cuda"),
              faces=torch.from_numpy(f_np).to("cuda"))
    _progress(ctx, 6)

    # ---- remove small connected components ----
    mesh.remove_small_connected_components(1e-5)
    mesh.fill_holes(max_hole_perimeter=3e-2)
    mesh.unify_face_orientations()
    _progress(ctx, 7)
    ctx.check_cancel()

    # ---- optional Taubin smoothing ----
    smoothing = ctx.params.get("refine_smoothing", "light")
    if smoothing and smoothing != "off":
        v, f = mesh.read()
        v_np = v.cpu().numpy() if hasattr(v, "cpu") else np.asarray(v)
        f_np = f.cpu().numpy() if hasattr(f, "cpu") else np.asarray(f)
        iters = 3 if smoothing == "light" else 8
        v_np = _taubin_smooth(v_np, f_np, iterations=iters)
        mesh = cumesh.CuMesh()
        mesh.init(vertices=torch.from_numpy(v_np).to("cuda"),
                  faces=torch.from_numpy(f_np).to("cuda"))
        print(f"[Refine/Repair] Taubin smoothing ({smoothing}, {iters} iters) applied",
              flush=True)
    _progress(ctx, 8)

    # ---- recompute vertex normals ----
    mesh.compute_vertex_normals()
    v, f = mesh.read()
    n = mesh.read_vertex_normals()
    v_np = v.cpu().numpy() if hasattr(v, "cpu") else np.asarray(v)
    f_np = f.cpu().numpy() if hasattr(f, "cpu") else np.asarray(f)
    n_np = n.cpu().numpy() if hasattr(n, "cpu") else np.asarray(n)
    ctx.hp_vertices = v_np.astype(np.float32)
    ctx.hp_faces = f_np.astype(np.int32)
    ctx.hp_normals = n_np.astype(np.float32)
    print(f"[Refine/Repair] cleaned high-poly: {len(v_np)} verts, {len(f_np)} faces",
          flush=True)
    _progress(ctx, 9)

    # ---- build BVH on cleaned high-poly ----
    ctx.bvh = cumesh.cuBVH(
        torch.from_numpy(ctx.hp_vertices).to("cuda"),
        torch.from_numpy(ctx.hp_faces).to("cuda"),
    )
    _progress(ctx, 10)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _weld_vertices(v: np.ndarray, f: np.ndarray, tol: float = 1e-6):
    """Merge vertices closer than ``tol`` and remap faces."""
    if len(v) == 0:
        return v, f
    # Snap to a grid at ``tol`` resolution, then dedupe.
    keys = np.round(v / tol).astype(np.int64)
    _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    if counts.shape[0] == v.shape[0]:
        return v, f  # nothing to weld
    # Average welded vertices (better than picking first).
    new_v = np.zeros((counts.shape[0], 3), dtype=v.dtype)
    np.add.at(new_v, inverse, v)
    new_v /= counts[:, None]
    new_f = inverse[f]
    # Drop degenerate faces that collapsed to a single vertex/edge.
    keep = (new_f[:, 0] != new_f[:, 1]) & (new_f[:, 1] != new_f[:, 2]) & (new_f[:, 0] != new_f[:, 2])
    return new_v, new_f[keep]


def _remove_degenerate_faces(v: np.ndarray, f: np.ndarray, min_area: float = 1e-12):
    """Drop triangles with near-zero area."""
    if len(f) == 0:
        return f
    a = v[f[:, 0]]
    b = v[f[:, 1]]
    c = v[f[:, 2]]
    cross = np.cross(b - a, c - a)
    area = 0.5 * np.linalg.norm(cross, axis=1)
    keep = area > min_area
    return f[keep]


def _laplacian_step(v: np.ndarray, f: np.ndarray, lam: float) -> np.ndarray:
    """One Laplacian smoothing step: move each vertex toward the centroid of
    its neighbors by factor ``lam``."""
    # Build neighbor sum via edge list.
    e0 = f[:, 0]
    e1 = f[:, 1]
    e2 = f[:, 2]
    n = len(v)
    neighbor_sum = np.zeros_like(v)
    np.add.at(neighbor_sum, e0, v[e1] + v[e2])
    np.add.at(neighbor_sum, e1, v[e2] + v[e0])
    np.add.at(neighbor_sum, e2, v[e0] + v[e1])
    neighbor_count = np.zeros(n, dtype=v.dtype)
    np.add.at(neighbor_count, e0, 2)
    np.add.at(neighbor_count, e1, 2)
    np.add.at(neighbor_count, e2, 2)
    centroid = np.where(neighbor_count[:, None] > 0,
                        neighbor_sum / np.maximum(neighbor_count[:, None], 1), v)
    return v + lam * (centroid - v)


def _taubin_smooth(v: np.ndarray, f: np.ndarray,
                   iterations: int = 3,
                   lam: float = 0.5, mu: float = -0.53) -> np.ndarray:
    """Taubin smoothing (feature-preserving): alternating Laplacian steps with
    positive ``lam`` then negative ``mu``. Avoids the shrinkage of plain
    Laplacian smoothing."""
    out = v.copy()
    for _ in range(iterations):
        out = _laplacian_step(out, f, lam)
        out = _laplacian_step(out, f, mu)
    return out
