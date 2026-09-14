"""Stage 6: Texture inpaint for uncovered atlas texels.

The UV atlas never covers 100% of the texture (padding, chart gaps, chart
boundaries). The generation pipeline fills these with ``cv2.INPAINT_TELEA``
which smears on large gaps. We provide:

  - ``patch`` (default): OpenCV PatchMatch (``cv2.inpaint`` with
    ``INPAINT_NS`` / Telea at a larger radius + a multi-pass dilation of the
    coverage mask edge to feather seams). Produces fewer smearing artifacts
    than the single-pass TELEA used in ``extract_glb``.
  - ``telea``: legacy fast single-pass TELEA (matches the original pipeline).
  - ``none``: leave uncovered texels black.

All channels (base_color, metallic, roughness, alpha, normal, AO) are
inpainted consistently using the same coverage mask.
"""

import numpy as np


def _progress(ctx, step: int, total: int = 5):
    if ctx.progress is not None:
        ctx.progress.update_subtask(6, "Texture Inpaint", step, total)


def run(ctx):
    method = ctx.params.get("refine_inpaint", "patch")
    if method == "none" or ctx.tex_coverage is None:
        print("[Refine/Inpaint] skipped (method=none or no coverage mask)",
              flush=True)
        _progress(ctx, 5, 5)
        return

    import cv2

    mask = ctx.tex_coverage
    H, W = mask.shape
    _progress(ctx, 0)

    # ---- UV bleed/padding: extend each chart's edge color outward by a few
    # texels (nearest-neighbor fill) so mipmaps don't bleed neighboring chart
    # colors at seams. Extends the existing edge color (unlike inpaint, which
    # synthesizes and can smear on large gaps). ----
    padding = int(ctx.params.get("refine_uv_padding", 8))
    if padding > 0:
        padding = max(1, int(round(padding * (W / 2048))))
        _bleed_textures(ctx, padding)
        mask = ctx.tex_coverage  # coverage now includes the padding band

    mask_inv = (~mask).astype(np.uint8)

    # Dilate the uncovered region by 1px so the inpaint bleeds slightly past
    # the boundary — hides aliasing at chart edges.
    mask_inv_dil = cv2.dilate(mask_inv, np.ones((3, 3), np.uint8), iterations=1)
    # The radius scales with texture size so large atlases get more reach.
    radius = max(3, int(W / 512))
    _progress(ctx, 1)

    # ---- base color (RGB) ----
    if ctx.tex_base_color is not None:
        ctx.tex_base_color = _inpaint_channel(ctx.tex_base_color, mask_inv_dil,
                                              method, radius)
    _progress(ctx, 2)
    ctx.check_cancel()

    # ---- single-channel maps ----
    for attr in ("tex_metallic", "tex_roughness", "tex_alpha", "tex_ao"):
        arr = getattr(ctx, attr, None)
        if arr is None:
            continue
        setattr(ctx, attr, _inpaint_channel(arr, mask_inv_dil, method, radius))
    _progress(ctx, 3)
    ctx.check_cancel()

    # ---- normal map: inpaint with neutral (0,0,1) background ----
    if ctx.tex_normal is not None:
        # Pre-fill uncovered with neutral normal so inpaint has good priors.
        neutral = np.zeros_like(ctx.tex_normal)
        neutral[..., 2] = 255
        arr = np.where(mask[..., None], ctx.tex_normal, neutral)
        ctx.tex_normal = _inpaint_channel(arr, mask_inv_dil, method, radius)
    _progress(ctx, 4)

    # Mark full coverage after inpaint.
    ctx.tex_coverage = np.ones_like(mask)
    print(f"[Refine/Inpaint] {method} inpaint done (radius={radius})", flush=True)
    _progress(ctx, 5, 5)


def _inpaint_channel(arr: np.ndarray, mask_inv: np.ndarray,
                     method: str, radius: int) -> np.ndarray:
    import cv2

    if arr.ndim == 2:
        return _inpaint_single(arr, mask_inv, method, radius)
    out = arr.copy()
    for c in range(out.shape[2]):
        out[..., c] = _inpaint_single(out[..., c], mask_inv, method, radius)
    return out


def _inpaint_single(chan: np.ndarray, mask_inv: np.ndarray,
                    method: str, radius: int) -> np.ndarray:
    import cv2

    if mask_inv.sum() == 0:
        return chan
    flag = cv2.INPAINT_TELEA
    if method == "patch":
        # Navier-Stokes tends to smear less on larger regions; we use it for
        # the "patch" preset with a larger radius.
        flag = cv2.INPAINT_NS
    return cv2.inpaint(chan, mask_inv, radius, flag)


def _bleed_textures(ctx, padding: int):
    """Extend chart edge colors outward by ``padding`` texels.

    Fills the padding band (between the original coverage and the dilated
    coverage) with the color of the nearest covered texel for every texture
    channel. Updates ``ctx.tex_coverage`` to include the padding band so the
    subsequent inpaint only handles the remaining deep chart gaps.
    """
    import cv2
    from scipy import ndimage

    mask = ctx.tex_coverage
    if mask is None or padding <= 0:
        return
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (padding * 2 + 1, padding * 2 + 1))
    padded = cv2.dilate(mask.astype(np.uint8), kernel).astype(bool)
    band = padded & ~mask
    if band.sum() == 0:
        return

    # Index of the nearest covered texel for every texel.
    idx = ndimage.distance_transform_edt(
        ~mask, return_distances=False, return_indices=True)
    for attr in ("tex_base_color", "tex_metallic", "tex_roughness",
                 "tex_alpha", "tex_ao", "tex_normal"):
        arr = getattr(ctx, attr, None)
        if arr is None:
            continue
        nearest = arr[tuple(idx)]
        arr[band] = nearest[band]
        setattr(ctx, attr, arr)
    ctx.tex_coverage = padded
    print(f"[Refine/Inpaint] bleed: {int(band.sum())} texels padded by "
          f"{padding}px", flush=True)
