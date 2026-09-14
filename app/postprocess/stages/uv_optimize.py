"""Stage 4: UV unwrap + atlas packing.

Primary path uses ``xatlas`` for high-quality chart packing. Falls back to
CuMesh's chart-based UV unwrap (the same one used by ``extract_glb``) if
xatlas is unavailable.
"""

import numpy as np
import torch


def _progress(ctx, step: int, total: int = 8):
    if ctx.progress is not None:
        ctx.progress.update_subtask(4, "UV Optimize", step, total)


def run(ctx):
    import cumesh

    packer = ctx.params.get("refine_uv_packer", "xatlas")
    _progress(ctx, 0)

    v = ctx.lp_vertices
    f = ctx.lp_faces

    if packer == "xatlas":
        try:
            uvs, faces_out, vmap = _xatlas_unwrap(v, f)
            _remap_lowpoly(ctx, uvs, faces_out, vmap)
            print(f"[Refine/UV] xatlas: {len(ctx.lp_vertices)} verts, "
                  f"{len(ctx.lp_faces)} faces", flush=True)
        except ImportError:
            ctx.warn("xatlas not installed; falling back to cumesh UV unwrap")
            _cumesh_unwrap(ctx)
        except Exception as e:
            ctx.warn(f"xatlas failed ({e}); falling back to cumesh UV unwrap")
            _cumesh_unwrap(ctx)
    else:
        # Fallback: CuMesh chart unwrap.
        _cumesh_unwrap(ctx)

    # ---- auto hard-edge detection: split normals (creases) along sharp
    # dihedral edges so hard-surface assets shade with crisp edges. ----
    _split_hard_edges(ctx)
    _progress(ctx, 8, 8)


def _xatlas_unwrap(v: np.ndarray, f: np.ndarray):
    """Run xatlas atlas packing. Returns (uvs, faces, vmap).

    xatlas may split vertices along seams (returning more vertices than the
    input) and remap faces; ``vmap`` maps new vertices back to source vertices
    so we can transfer normals/positions.
    """
    import xatlas

    atlas = xatlas.Atlas()
    atlas.add_mesh(v, f)
    # Use defaults — ChartOptions()/PackOptions() in pyxatlas take no kwargs.
    atlas.generate()
    mesh = atlas.get_mesh(0)
    # xatlas >=0.1 returns a (vertex_map, indices, uvs) tuple; older builds
    # returned an object with .vertex_map/.face/.uv attributes.
    if isinstance(mesh, tuple):
        vmap, faces_out, uvs = mesh
        vmap = np.ascontiguousarray(vmap, dtype=np.int32)
        faces_out = np.ascontiguousarray(faces_out, dtype=np.int32)
        uvs = np.ascontiguousarray(uvs, dtype=np.float32)
    else:
        vmap = np.ascontiguousarray(mesh.vertex_map, dtype=np.int32)
        faces_out = np.ascontiguousarray(mesh.face, dtype=np.int32)
        uvs = np.ascontiguousarray(mesh.uv, dtype=np.float32)
    return uvs, faces_out, vmap


def _remap_lowpoly(ctx, uvs, faces_out, vmap):
    """After xatlas splits vertices, rebuild lp_vertices/faces/normals and
    store UVs. Positions and normals come from the source vertex each new
    vertex maps to."""
    ctx.lp_vertices = ctx.lp_vertices[vmap].astype(np.float32)
    if ctx.lp_normals is not None:
        ctx.lp_normals = ctx.lp_normals[vmap].astype(np.float32)
    ctx.lp_faces = faces_out
    ctx.lp_uvs = uvs


def _cumesh_unwrap(ctx):
    """Fallback UV unwrap via CuMesh (same algorithm as the generation path)."""
    import cumesh

    mesh = cumesh.CuMesh()
    mesh.init(vertices=torch.from_numpy(ctx.lp_vertices).to("cuda"),
              faces=torch.from_numpy(ctx.lp_faces).to("cuda"))
    if ctx.lp_normals is None:
        mesh.compute_vertex_normals()

    out_vertices, out_faces, out_uvs, out_vmaps = mesh.uv_unwrap(
        compute_charts_kwargs={
            "threshold_cone_half_angle_rad": np.radians(120.0),
            "refine_iterations": 0,
            "global_iterations": 1,
            "smooth_strength": 1,
        },
        return_vmaps=True,
        verbose=True,
    )
    out_vertices = out_vertices.to("cuda")
    out_faces = out_faces.to("cuda")
    out_uvs = out_uvs.to("cuda")
    out_vmaps = out_vmaps.to("cuda")

    mesh.compute_vertex_normals()
    out_normals = mesh.read_vertex_normals()[out_vmaps]

    ctx.lp_vertices = out_vertices.cpu().numpy().astype(np.float32)
    ctx.lp_faces = out_faces.cpu().numpy().astype(np.int32)
    ctx.lp_normals = out_normals.cpu().numpy().astype(np.float32)
    ctx.lp_uvs = out_uvs.cpu().numpy().astype(np.float32)


def _split_hard_edges(ctx):
    """Split vertex normals (creases) along hard dihedral edges.

    Detects edges whose dihedral angle exceeds ``refine_hard_edge_angle``,
    then duplicates vertices so each side of a hard edge gets its own smooth
    normal. Position and UV are carried along per duplicated vertex; the
    per-group normals are recomputed as the area-weighted average of the
    incident face normals. This produces crisp edges for hard-surface assets
    instead of the "pillow" shading of fully smooth normals.
    """
    angle_deg = float(ctx.params.get("refine_hard_edge_angle", 60))
    if angle_deg <= 0:
        return

    v = ctx.lp_vertices
    f = ctx.lp_faces
    uvs = ctx.lp_uvs
    if v is None or f is None or uvs is None or len(f) == 0:
        return

    try:
        import trimesh
        tm = trimesh.Trimesh(vertices=v, faces=f, process=False)
        adjacency = np.asarray(tm.face_adjacency, dtype=np.int64)
        angles = np.asarray(tm.face_adjacency_angles, dtype=np.float64)
    except Exception as e:
        ctx.warn(f"hard-edge detection skipped ({e})")
        return

    if len(adjacency) == 0:
        return

    # Hard edges are those whose dihedral angle exceeds the threshold.
    smooth_pairs = adjacency[angles <= np.radians(angle_deg)]

    n_faces = len(f)
    n_corners = n_faces * 3
    parent = np.arange(n_corners, dtype=np.int64)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    # Union corners across smooth (non-hard) edges.
    for f1, f2 in smooth_pairs:
        for sv in np.intersect1d(f[f1], f[f2]):
            i1 = int(np.where(f[f1] == sv)[0][0])
            i2 = int(np.where(f[f2] == sv)[0][0])
            r1, r2 = find(f1 * 3 + i1), find(f2 * 3 + i2)
            if r1 != r2:
                parent[r2] = r1

    # Collapse all corners to their root group (vectorized path compression).
    roots = parent.copy()
    while not np.array_equal(roots, roots[roots]):
        roots = roots[roots]
    unique_groups, new_idx = np.unique(roots, return_inverse=True)
    n_new = len(unique_groups)
    new_f = new_idx.reshape(n_faces, 3).astype(np.int32)

    corner_vertex = f.reshape(-1)          # original vertex index per corner
    first = np.empty(n_new, dtype=np.int64)
    first[new_idx] = np.arange(n_corners)  # one representative corner per group

    new_v = v[corner_vertex[first]].astype(np.float32)
    new_uv = uvs[corner_vertex[first]].astype(np.float32)

    # Area-weighted vertex normals per group (cross product magnitude = 2*area).
    a = v[f[:, 0]]
    b = v[f[:, 1]]
    c = v[f[:, 2]]
    face_n = np.cross(b - a, c - a)        # (n_faces, 3), area-weighted
    corner_n = np.repeat(face_n, 3, axis=0)  # (n_corners, 3)
    new_n = np.zeros((n_new, 3), dtype=np.float32)
    np.add.at(new_n, new_idx, corner_n.astype(np.float32))
    norm = np.linalg.norm(new_n, axis=-1, keepdims=True)
    new_n = new_n / (norm + 1e-12)

    if n_new > len(v):
        print(f"[Refine/UV] hard-edge split: {len(v)} -> {n_new} verts "
              f"(angle > {angle_deg}deg)", flush=True)
    ctx.lp_vertices = new_v
    ctx.lp_faces = new_f
    ctx.lp_uvs = new_uv
    ctx.lp_normals = new_n
