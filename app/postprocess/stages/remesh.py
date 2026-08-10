"""Stage 2: Isotropic remesh.

Uniform-triangle isotropic remeshing produces evenly sized triangles, which
improves shading quality, normal-map fidelity, and UV unwrap. We use pymeshlab
when available (MeshLab's ``meshing_isotropic_explicit_remeshing`` filter).

Because the cleaned high-poly can be millions of faces, we first pre-reduce it
with CuMesh quadric decimation to a tractable intermediate count before running
pymeshlab (isotropic remeshing does not handle huge density reductions well).
If pymeshlab is unavailable or fails, we fall back to no-op and rely on the
decimation stage for triangle quality.
"""

import numpy as np
import torch


def _progress(ctx, step: int, total: int = 5):
    if ctx.progress is not None:
        ctx.progress.update_subtask(2, "Isotropic Remesh", step, total)


def run(ctx):
    if not ctx.params.get("refine_remesh", True):
        print("[Refine/Remesh] disabled by params, skipping", flush=True)
        _progress(ctx, 5, 5)
        return

    v = ctx.hp_vertices
    f = ctx.hp_faces
    _progress(ctx, 0)

    target = int(ctx.params.get("decimation_target", 100000))
    # Target edge length: derive from the average edge length that would give
    # roughly 2x the target triangle count (remesh slightly denser than the
    # final decimation target so decimation has room to optimize).
    surface_area = _mesh_surface_area(v, f)
    target_tris = max(target * 2, 50000)
    # Equilateral triangle area = (sqrt(3)/4) * L^2  ->  L = sqrt(4A / (sqrt(3) N))
    L = float(np.sqrt(4.0 * surface_area / (np.sqrt(3.0) * target_tris)))
    if L <= 0:
        ctx.warn("remesh target edge length was non-positive; skipping remesh")
        ctx.lp_vertices = v.astype(np.float32)
        ctx.lp_faces = f.astype(np.int32)
        _progress(ctx, 5, 5)
        return

    print(f"[Refine/Remesh] target edge length = {L:.6f} (for ~{target_tris} tris)",
          flush=True)
    _progress(ctx, 1)
    ctx.check_cancel()

    # ---- pre-reduce extremely dense high-poly meshes ----
    # CuMesh quadric decimation is GPU and fast; pymeshlab is not. Cap the
    # remesh input at ~1M faces so the filter stays responsive.
    MAX_REMESH_INPUT = 1_000_000
    if len(f) > MAX_REMESH_INPUT:
        reduce_to = max(int(target * 4), 200_000)
        if reduce_to < len(f):
            # On Blackwell, CuMesh simplify hangs — use CPU trimesh instead
            use_cumesh = True
            try:
                import torch
                if torch.cuda.is_available() and torch.cuda.get_device_capability()[0] >= 12:
                    use_cumesh = False
            except Exception:
                pass

            if use_cumesh:
                import cumesh
                mesh = cumesh.CuMesh()
                mesh.init(vertices=torch.from_numpy(v).to("cuda"),
                          faces=torch.from_numpy(f).to("cuda"))
                print(f"[Refine/Remesh] pre-reducing {len(f)} faces -> {reduce_to} "
                      f"before remesh (CuMesh)", flush=True)
                from app.pipeline import _safe_simplify
                _safe_simplify(mesh, reduce_to)
                out_v, out_f = mesh.read()
                v = (out_v.cpu().numpy() if hasattr(out_v, "cpu") else np.asarray(out_v)).astype(np.float32)
                f = (out_f.cpu().numpy() if hasattr(out_f, "cpu") else np.asarray(out_f)).astype(np.int32)
            else:
                import trimesh
                print(f"[Refine/Remesh] pre-reducing {len(f)} faces -> {reduce_to} "
                      f"before remesh (CPU trimesh, Blackwell)", flush=True)
                tm = trimesh.Trimesh(vertices=v, faces=f, process=False)
                tm = tm.simplify_quadric_decimation(face_count=reduce_to)
                v = np.asarray(tm.vertices, dtype=np.float32)
                f = np.asarray(tm.faces, dtype=np.int32)
            print(f"[Refine/Remesh] pre-reduced: {len(v)} verts, {len(f)} faces",
                  flush=True)
    _progress(ctx, 2)
    ctx.check_cancel()

    # ---- pymeshlab isotropic remesh ----
    try:
        new_v, new_f = _remesh_pymeshlab(v, f, target_len=L, iterations=2)
    except Exception as e:
        ctx.warn(f"pymeshlab remesh failed ({e}); falling back to no-op remesh")
        new_v, new_f = v, f

    _progress(ctx, 3)
    ctx.check_cancel()

    # ---- sanity: never let remesh blow up the triangle count ----
    if len(new_f) > target * 8:
        ctx.warn(f"remesh produced {len(new_f)} faces (>{target*8}); using "
                 "pre-reduced input instead")
        new_v, new_f = v, f

    ctx.lp_vertices = new_v.astype(np.float32)
    ctx.lp_faces = new_f.astype(np.int32)
    print(f"[Refine/Remesh] remeshed: {len(new_v)} verts, {len(new_f)} faces",
          flush=True)
    _progress(ctx, 5, 5)


def _mesh_surface_area(v: np.ndarray, f: np.ndarray) -> float:
    if len(f) == 0:
        return 0.0
    a = v[f[:, 0]]
    b = v[f[:, 1]]
    c = v[f[:, 2]]
    cross = np.cross(b - a, c - a)
    return float(0.5 * np.linalg.norm(cross, axis=1).sum())


def _remesh_pymeshlab(v: np.ndarray, f: np.ndarray,
                      target_len: float, iterations: int = 2):
    """Isotropic remesh via pymeshlab. Raises if pymeshlab unavailable.
    
    Also runs edge-flip optimization passes for better triangle quality
    and edge flow alignment with curvature directions."""
    import pymeshlab

    ms = pymeshlab.MeshSet()
    m = pymeshlab.Mesh(vertex_matrix=v.astype(np.float64),
                       face_matrix=f.astype(np.int32))
    ms.add_mesh(m, "remesh_input")

    # Isotropic remeshing — uniform triangle size
    ms.apply_filter("meshing_isotropic_explicit_remeshing",
                    targetlen=pymeshlab.PercentageValue(target_len * 100),
                    iterations=iterations)

    # Edge flip by curvature — aligns edges with surface curvature,
    # producing better edge flow and more regular triangulation
    try:
        ms.apply_filter("meshing_edge_flip_by_curvature_optimization")
    except Exception:
        pass

    # Edge flip by planarity — in flat areas, flips edges to create
    # more regular quad-like patterns
    try:
        ms.apply_filter("meshing_edge_flip_by_planar_optimization")
    except Exception:
        pass

    out_m = ms.current_mesh()
    return (out_m.vertex_matrix().astype(np.float32),
            out_m.face_matrix().astype(np.int32))
