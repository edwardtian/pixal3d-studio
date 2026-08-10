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
            _progress(ctx, 8, 8)
            return
        except ImportError:
            ctx.warn("xatlas not installed; falling back to cumesh UV unwrap")
        except Exception as e:
            ctx.warn(f"xatlas failed ({e}); falling back to cumesh UV unwrap")

    # Fallback: CuMesh chart unwrap.
    _cumesh_unwrap(ctx)
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
    vmesh = atlas.get_mesh(0)
    uvs = np.ascontiguousarray(vmesh.uv, dtype=np.float32)
    faces_out = np.ascontiguousarray(vmesh.face, dtype=np.int32)
    vmap = np.ascontiguousarray(vmesh.vertex_map, dtype=np.int32)
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
