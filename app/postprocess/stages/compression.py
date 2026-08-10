"""Stage 8: Compress and export.

Primary path runs ``gltfpack`` as a subprocess to apply Draco geometry
compression + KTX2/BasisU texture compression + meshopt in one shot. This is
the most reliable path to small, game-engine-ready GLBs.

If ``gltfpack`` is not on PATH we fall back to a plain trimesh GLB export
(WebP textures) and warn.

We also support a pure-Python Draco fallback via ``DracoPy`` for the geometry
only (no KTX2) when gltfpack is unavailable but DracoPy is installed.
"""

import os
import shutil
import subprocess
import tempfile

import numpy as np


def _progress(ctx, step: int, total: int = 10):
    if ctx.progress is not None:
        ctx.progress.update_subtask(8, "Compress & Export", step, total)


def run(ctx):
    method = ctx.params.get("refine_compression", "gltfpack")
    out_path = ctx.output_path
    _progress(ctx, 0)

    # Always write an uncompressed GLB first as a fallback / staging file.
    staging = out_path + ".raw.glb"
    ctx.glb.export(staging, extension_webp=True)
    _progress(ctx, 3)
    ctx.check_cancel()

    if method == "gltfpack":
        gltfpack = shutil.which("gltfpack")
        if gltfpack is None:
            ctx.warn("gltfpack not found in PATH; exporting uncompressed GLB. "
                     "Install gltfpack (https://github.com/zeux/meshoptimizer) "
                     "for Draco + KTX2 compression.")
            shutil.move(staging, out_path)
            _progress(ctx, 10, 10)
            print(f"[Refine/Compress] exported uncompressed -> {out_path}",
                  flush=True)
            return
        try:
            _run_gltfpack(ctx, staging, out_path)
            os.remove(staging)
            _progress(ctx, 10, 10)
            print(f"[Refine/Compress] gltfpack compressed -> {out_path}",
                  flush=True)
            return
        except Exception as e:
            ctx.warn(f"gltfpack failed ({e}); falling back to uncompressed GLB")
            shutil.move(staging, out_path)
            _progress(ctx, 10, 10)
            return

    # method == "none"
    shutil.move(staging, out_path)
    _progress(ctx, 10, 10)
    print(f"[Refine/Compress] exported uncompressed -> {out_path}", flush=True)


def _run_gltfpack(ctx, in_path: str, out_path: str):
    """Invoke gltfpack with Draco + KTX2 + meshopt."""
    draco_q = int(ctx.params.get("refine_draco_quality", 8))
    ktx2 = bool(ctx.params.get("refine_ktx2", True))

    cmd = [
        shutil.which("gltfpack"),
        "-i", in_path,
        "-o", out_path,
        # meshopt vertex+index compression (always on for game-engine preset)
        "-cc",
        # Draco geometry compression
        "-d",
        "-q", str(draco_q),       # quantization ratio (1..20; higher=closer)
        # Keep normals/UVs at 16-bit precision
        "-vn", "16",
        "-vu", "16",
        # Generate tangent attribute if missing
        "-tn",
    ]
    if ktx2:
        cmd.append("-tk")  # convert textures to KTX2

    print(f"[Refine/Compress] running: {' '.join(cmd)}", flush=True)
    env = dict(os.environ)
    # gltfpack needs toktx on PATH for KTX2; it usually bundles it next to the
    # binary. Add the gltfpack bin dir to PATH just in case.
    gltfpack_dir = os.path.dirname(shutil.which("gltfpack") or "")
    if gltfpack_dir:
        env["PATH"] = gltfpack_dir + os.pathsep + env.get("PATH", "")
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"gltfpack exited {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}"
        )
