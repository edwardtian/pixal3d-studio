"""Stage 9: Validate the refined mesh before shipping.

Runs a set of checks against the exported GLB and the in-memory mesh. Failures
are recorded in ``ctx.validation_report``; hard failures raise, soft failures
become warnings so the user still gets a downloadable file with diagnostics.
"""

import os
import numpy as np


def _progress(ctx, step: int, total: int = 3):
    if ctx.progress is not None:
        ctx.progress.update_subtask(9, "Validate", step, total)


def run(ctx):
    report = ctx.validation_report
    _progress(ctx, 0)

    # ---- geometry checks on the low-poly (pre-axis-transform) ----
    v = ctx.lp_vertices
    f = ctx.lp_faces
    report["vertices"] = int(len(v)) if v is not None else 0
    report["faces"] = int(len(f)) if f is not None else 0
    report["texture_size"] = int(ctx.texture_size)

    if v is not None and f is not None and len(f) > 0:
        report["manifold"] = _check_manifold(f)
        report["watertight"] = _check_watertight(f)
        report["degenerate_faces"] = int(_count_degenerate(v, f))
        report["duplicate_vertices"] = int(_count_duplicate_verts(v))
    _progress(ctx, 1)

    # ---- texture checks ----
    if ctx.tex_coverage is not None:
        coverage = float(ctx.tex_coverage.mean())
        report["uv_coverage"] = coverage
        if coverage < 0.99:
            ctx.warn(f"UV coverage is {coverage*100:.1f}% (some texels may be unpainted)")
    report["has_normal_map"] = ctx.tex_normal is not None
    report["has_ao_map"] = ctx.tex_ao is not None
    report["has_tangents"] = ctx.lp_tangents is not None
    report["alpha_mode"] = ctx.alpha_mode
    _progress(ctx, 2)

    # ---- file checks ----
    if os.path.exists(ctx.output_path):
        size_mb = os.path.getsize(ctx.output_path) / (1024 * 1024)
        report["file_size_mb"] = round(size_mb, 2)
        if size_mb > 50:
            ctx.warn(f"Refined GLB is large ({size_mb:.1f} MB); consider lower "
                     "decimation_target or texture_size")
    _progress(ctx, 3, 3)

    print(f"[Refine/Validate] {report}", flush=True)
    if ctx.warnings:
        print(f"[Refine/Validate] warnings: {ctx.warnings}", flush=True)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _check_manifold(f: np.ndarray) -> bool:
    """A manifold mesh has every edge shared by exactly 2 faces (closed) or
    1 face (boundary). Here we flag edges shared by >2 faces as non-manifold."""
    edges = np.concatenate([
        np.sort(f[:, [0, 1]], axis=1),
        np.sort(f[:, [1, 2]], axis=1),
        np.sort(f[:, [2, 0]], axis=1),
    ], axis=0)
    # Count occurrences of each edge.
    e_view = edges[:, 0].astype(np.int64) * (edges[:, 0].max() + 1) + edges[:, 1]
    _, counts = np.unique(e_view, return_counts=True)
    return bool((counts <= 2).all())


def _check_watertight(f: np.ndarray) -> bool:
    """Watertight = every edge is shared by exactly 2 faces (no boundaries)."""
    edges = np.concatenate([
        np.sort(f[:, [0, 1]], axis=1),
        np.sort(f[:, [1, 2]], axis=1),
        np.sort(f[:, [2, 0]], axis=1),
    ], axis=0)
    e_view = edges[:, 0].astype(np.int64) * (edges[:, 0].max() + 1) + edges[:, 1]
    _, counts = np.unique(e_view, return_counts=True)
    return bool((counts == 2).all())


def _count_degenerate(v: np.ndarray, f: np.ndarray) -> int:
    a = v[f[:, 0]]
    b = v[f[:, 1]]
    c = v[f[:, 2]]
    cross = np.cross(b - a, c - a)
    area = 0.5 * np.linalg.norm(cross, axis=1)
    return int((area < 1e-12).sum())


def _count_duplicate_verts(v: np.ndarray, tol: float = 1e-6) -> int:
    if len(v) == 0:
        return 0
    keys = np.round(v / tol).astype(np.int64)
    _, counts = np.unique(keys, axis=0, return_counts=True)
    return int((counts > 1).sum())
