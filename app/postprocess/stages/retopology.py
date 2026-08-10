"""Stage 3: Curvature-aware decimation.

Reduce the remeshed low-poly to the target triangle count. We use CuMesh's
quadric-error decimation (``mesh.simplify``) which already weights by surface
error — combined with the prior isotropic remesh this preserves silhouettes
and feature edges far better than the generation pipeline's uniform decimate.

If the remesh stage was skipped, we seed the low-poly from the cleaned
high-poly here. We then run a two-pass decimation (3x then 1x target) with a
topology cleanup between passes, mirroring the proven approach in
``extract_glb`` but with an extra final cleanup.
"""

import numpy as np
import torch


def _progress(ctx, step: int, total: int = 5):
    if ctx.progress is not None:
        ctx.progress.update_subtask(3, "Curvature Decimation", step, total)


def run(ctx):
    target = int(ctx.params.get("decimation_target", 100000))

    # Seed low-poly from remesh output if present, else from cleaned high-poly.
    if ctx.lp_vertices is None:
        ctx.lp_vertices = ctx.hp_vertices.copy()
        ctx.lp_faces = ctx.hp_faces.copy()
    _progress(ctx, 0)

    # Check if CuMesh is likely to hang (Blackwell sm_120).
    # On Blackwell, CuMesh's simplify CUDA kernel hangs, locking the entire GPU.
    # We detect this and skip straight to CPU-based decimation.
    use_cumesh = True
    try:
        import torch
        if torch.cuda.is_available():
            cap = torch.cuda.get_device_capability()
            if cap[0] >= 12:  # sm_120+ (Blackwell)
                use_cumesh = False
                print("[Refine/Decimate] Blackwell GPU detected — using CPU decimation "
                      "to avoid CuMesh CUDA hang", flush=True)
    except Exception:
        pass

    if use_cumesh:
        _run_cumesh_decimate(ctx, target)
    else:
        _cpu_decimate(ctx, target)

    _progress(ctx, 5, 5)


def _run_cumesh_decimate(ctx, target):
    """GPU-based decimation using CuMesh. Falls back to CPU on failure."""
    import cumesh

    try:
        mesh = cumesh.CuMesh()
        mesh.init(vertices=torch.from_numpy(ctx.lp_vertices).to("cuda"),
                  faces=torch.from_numpy(ctx.lp_faces).to("cuda"))
        print(f"[Refine/Decimate] start: {mesh.num_vertices} verts, "
              f"{mesh.num_faces} faces, target={target}", flush=True)

        if mesh.num_faces <= target:
            print(f"[Refine/Decimate] already under target, skipping", flush=True)
            v, f = mesh.read()
            ctx.lp_vertices = (v.cpu().numpy() if hasattr(v, "cpu") else np.asarray(v)).astype(np.float32)
            ctx.lp_faces = (f.cpu().numpy() if hasattr(f, "cpu") else np.asarray(f)).astype(np.int32)
            _progress(ctx, 5, 5)
            return

        # ---- pass 1: decimate to 3x target ----
        _progress(ctx, 1)
        _safe_simplify(mesh, target * 3)
        print(f"[Refine/Decimate] pass 1 (3x): {mesh.num_vertices} verts, "
              f"{mesh.num_faces} faces", flush=True)
        _progress(ctx, 2)
        ctx.check_cancel()

        # ---- topology cleanup between passes ----
        mesh.remove_duplicate_faces()
        mesh.repair_non_manifold_edges()
        mesh.remove_small_connected_components(1e-5)
        mesh.fill_holes(max_hole_perimeter=3e-2)
        _progress(ctx, 3)

        # ---- pass 2: decimate to target ----
        _safe_simplify(mesh, target)
        print(f"[Refine/Decimate] pass 2 (1x): {mesh.num_vertices} verts, "
              f"{mesh.num_faces} faces", flush=True)
        _progress(ctx, 4)
        ctx.check_cancel()

        # ---- final cleanup ----
        mesh.remove_duplicate_faces()
        mesh.repair_non_manifold_edges()
        mesh.remove_small_connected_components(1e-5)
        mesh.fill_holes(max_hole_perimeter=3e-2)
        mesh.unify_face_orientations()

        if ctx.params.get("refine_watertight", True):
            _try_make_watertight(mesh, ctx)

        mesh.compute_vertex_normals()
        v, f = mesh.read()
        n = mesh.read_vertex_normals()
        ctx.lp_vertices = (v.cpu().numpy() if hasattr(v, "cpu") else np.asarray(v)).astype(np.float32)
        ctx.lp_faces = (f.cpu().numpy() if hasattr(f, "cpu") else np.asarray(f)).astype(np.int32)
        ctx.lp_normals = (n.cpu().numpy() if hasattr(n, "cpu") else np.asarray(n)).astype(np.float32)
        print(f"[Refine/Decimate] final: {len(ctx.lp_vertices)} verts, "
              f"{len(ctx.lp_faces)} faces", flush=True)

    except Exception as e:
        print(f"[Refine/Decimate] CuMesh failed ({e}), falling back to CPU decimation", flush=True)
        _cpu_decimate(ctx, target)

    _progress(ctx, 5, 5)


def _safe_simplify(mesh, target, timeout_sec=120):
    """Run CuMesh simplify with a thread-based timeout. If it hangs, fall back
    to CPU-based trimesh decimation."""
    import threading
    result = {"error": None, "done": False}

    def _do_simplify():
        try:
            mesh.simplify(target, verbose=True)
            result["done"] = True
        except Exception as e:
            result["error"] = e

    t = threading.Thread(target=_do_simplify, daemon=True)
    t.start()
    t.join(timeout=timeout_sec)

    if not result["done"]:
        if result["error"]:
            raise result["error"]
        raise TimeoutError(f"CuMesh simplify(target={target}) timed out after {timeout_sec}s")


def _cpu_decimate(ctx, target):
    """CPU-based fallback decimation using trimesh + pymeshlab edge flips."""
    import trimesh
    import numpy as np

    print(f"[Refine/Decimate/CPU] Using trimesh quadric decimation, target={target}", flush=True)
    tm = trimesh.Trimesh(vertices=ctx.lp_vertices, faces=ctx.lp_faces, process=False)
    print(f"[Refine/Decimate/CPU] start: {len(tm.vertices)} verts, {len(tm.faces)} faces", flush=True)
    _progress(ctx, 1)

    if len(tm.faces) > target * 3:
        print(f"[Refine/Decimate/CPU] pass 1: {len(tm.faces)} -> {target * 3} faces", flush=True)
        tm = tm.simplify_quadric_decimation(face_count=target * 3)
        print(f"[Refine/Decimate/CPU] pass 1 done: {len(tm.vertices)} verts, {len(tm.faces)} faces", flush=True)
        _progress(ctx, 2)

    _cleanup_trimesh(tm)
    _progress(ctx, 3)

    if len(tm.faces) > target:
        print(f"[Refine/Decimate/CPU] pass 2: {len(tm.faces)} -> {target} faces", flush=True)
        tm = tm.simplify_quadric_decimation(face_count=target)
        print(f"[Refine/Decimate/CPU] pass 2 done: {len(tm.vertices)} verts, {len(tm.faces)} faces", flush=True)
    _progress(ctx, 4)

    _cleanup_trimesh(tm)

    # Post-decimation edge flip optimization via pymeshlab — improves triangle
    # quality and aligns edges with curvature for better shading/normal-mapping
    v, f = _edge_flip_optimize(tm.vertices, tm.faces)
    ctx.lp_vertices = v
    ctx.lp_faces = f
    ctx.lp_normals = _compute_vertex_normals(v, f)
    print(f"[Refine/Decimate/CPU] final: {len(v)} verts, {len(f)} faces", flush=True)


def _edge_flip_optimize(vertices, faces):
    """Run pymeshlab edge-flip optimization for better triangle topology."""
    try:
        import pymeshlab
        import numpy as np
        ms = pymeshlab.MeshSet()
        ms.add_mesh(pymeshlab.Mesh(
            vertex_matrix=np.asarray(vertices, dtype=np.float64),
            face_matrix=np.asarray(faces, dtype=np.int32),
        ))
        # Flip edges to align with curvature — better edge flow
        ms.apply_filter("meshing_edge_flip_by_curvature_optimization")
        # Flip edges in flat areas — more regular triangulation
        ms.apply_filter("meshing_edge_flip_by_planar_optimization")
        m = ms.current_mesh()
        out_v = m.vertex_matrix().astype(np.float32)
        out_f = m.face_matrix().astype(np.int32)
        print(f"[Refine/Decimate] edge-flip: {len(out_v)} verts, {len(out_f)} faces", flush=True)
        return out_v, out_f
    except Exception as e:
        print(f"[Refine/Decimate] edge-flip skipped ({e})", flush=True)
        return np.asarray(vertices, dtype=np.float32), np.asarray(faces, dtype=np.int32)


def _compute_vertex_normals(v, f):
    """Compute per-vertex normals from faces."""
    import numpy as np
    a = v[f[:, 0]]
    b = v[f[:, 1]]
    c = v[f[:, 2]]
    fn = np.cross(b - a, c - a)
    fn = fn / (np.linalg.norm(fn, axis=-1, keepdims=True) + 1e-12)
    vn = np.zeros_like(v)
    np.add.at(vn, f[:, 0], fn)
    np.add.at(vn, f[:, 1], fn)
    np.add.at(vn, f[:, 2], fn)
    vn = vn / (np.linalg.norm(vn, axis=-1, keepdims=True) + 1e-12)
    return vn.astype(np.float32)


def _cleanup_trimesh(tm):
    """Remove duplicate faces, merge vertices, fill holes — compatible across
    trimesh versions (remove_duplicate_faces was removed in 4.x)."""
    tm.merge_vertices()
    # Remove duplicate faces: sort each face's vertex indices, find unique rows
    faces_sorted = np.sort(tm.faces, axis=1)
    _, unique_idx = np.unique(faces_sorted, axis=0, return_index=True)
    tm.update_faces(unique_idx)
    # Remove degenerate (zero-area) faces
    mask = tm.nondegenerate_faces()
    tm.update_faces(mask)
    tm.fill_holes()


def _try_make_watertight(mesh, ctx):
    """Best-effort watertight pass. We check boundary edges via trimesh and
    fill remaining holes; if it still has boundary edges we warn but continue
    (some meshes — open cloth, terrain — are intentionally non-watertight)."""
    try:
        import trimesh
        v, f = mesh.read()
        v_np = (v.cpu().numpy() if hasattr(v, "cpu") else np.asarray(v)).astype(np.float32)
        f_np = (f.cpu().numpy() if hasattr(f, "cpu") else np.asarray(f)).astype(np.int32)
        tm = trimesh.Trimesh(vertices=v_np, faces=f_np, process=False)
        if tm.is_watertight:
            return
        # Try filling any remaining holes via trimesh then push back.
        tm.fill_holes()
        if tm.is_watertight:
            import torch
            mesh.init(vertices=torch.from_numpy(tm.vertices).to("cuda"),
                      faces=torch.from_numpy(tm.faces).to("cuda"))
            return
        ctx.warn("mesh is not fully watertight after repair; exporting as-is")
    except Exception as e:
        ctx.warn(f"watertight check failed: {e}")
