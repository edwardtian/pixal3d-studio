"""TripoSG image-to-3D backend.

TripoSG (VAST-AI, "TripoSG: High-Fidelity 3D Shape Synthesis using
Large-Scale Rectified Flow Transformers") generates high-quality watertight
geometry from a single image. Output is geometry-only (no textures).

Implementation modeled on modly's TripoSG extension
(https://github.com/lightningpixel/modly-triposg-extension):

- model:     VAST-AI/TripoSG  (diffusers-style HF repo, not gated)
- pipeline:  triposg.pipelines.pipeline_triposg.TripoSGPipeline
- preprocess: RMBG-2.0 background removal + white composite + foreground resize
- mesh decode: DiffDMC (diso CUDA extension) or hierarchical marching cubes

Requirements (see Containerfile):
- the `triposg` package (https://github.com/VAST-AI-Research/TripoSG)
- `diso` (CUDA-compiled extension, required by triposg.inference_utils)
- transformers (RMBG-2.0), scikit-image, omegaconf, jaxtyping, typeguard, peft
"""

from __future__ import annotations

import math
import os
import time

import numpy as np
from PIL import Image

from app.backends.base import BackendBase, CancelledError
from app.config import settings


class TripoSGBackend(BackendBase):
    BACKEND_ID = "triposg"
    DISPLAY_NAME = "TripoSG"
    DESCRIPTION = (
        "VAST TripoSG — high-fidelity 3D shape synthesis from a single image "
        "via a large-scale rectified-flow transformer. The model generates "
        "geometry; a base-color texture + PBR material is then projected onto "
        "the mesh from the input photo (optional). ~8-10 GB VRAM."
    )
    VRAM_GB = 10
    SUPPORTS_TEXTURE = True
    SUPPORTS_REFINE = False

    def __init__(self) -> None:
        super().__init__()
        self._pipe = None
        self._rmbg = None
        self._moge = None
        self._device = "cuda"
    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    @classmethod
    def is_available(cls) -> bool:
        p = settings.TRIPOSG_MODEL_PATH
        if os.path.isabs(p) or os.path.isdir(p):
            return os.path.exists(p)
        try:
            from huggingface_hub import try_to_load_from_cache
            # Check both the repo metadata and the actual weights — a failed
            # first load can leave model_index.json cached without weights.
            return (
                try_to_load_from_cache(p, "model_index.json") is not None
                and try_to_load_from_cache(
                    p, "transformer/diffusion_pytorch_model.safetensors"
                ) is not None
            )
        except Exception:
            return True  # assume downloadable
    def params_schema(self) -> dict:
        from app.parameters import TRIPOSG_PARAMETER_DEFINITIONS
        return TRIPOSG_PARAMETER_DEFINITIONS

    def load(self) -> None:
        if self._loaded:
            return
        import torch
        try:
            from triposg.pipelines.pipeline_triposg import TripoSGPipeline
            from triposg.models.transformers.triposg_transformer import TripoSGDiTModel
            from triposg.models.autoencoders.autoencoder_kl_triposg import TripoSGVAEModel
            from triposg.schedulers.scheduling_rectified_flow import RectifiedFlowScheduler
        except ImportError as exc:
            raise RuntimeError(
                "TripoSG backend is not installed. The 'triposg' package "
                "(https://github.com/VAST-AI-Research/TripoSG) and the 'diso' "
                "CUDA extension must be importable — see the Containerfile."
            ) from exc

        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if device == "cuda" else torch.float32
        self._device = device

        model_path = settings.TRIPOSG_MODEL_PATH
        print(f"[TripoSG] Loading model from {model_path} ...")

        try:
            # diffusers <= 0.31 resolves the custom triposg classes referenced
            # by model_index.json via arbitrary module imports.
            self._pipe = TripoSGPipeline.from_pretrained(
                model_path, torch_dtype=dtype
            ).to(device, dtype)
            print("[TripoSG] Pipeline loaded via model_index.json.")
        except Exception as exc:
            # diffusers >= 0.32 no longer imports arbitrary module paths for
            # model_index.json components ("... is not a module in
            # 'diffusers/pipelines'"), so build the pipeline manually from its
            # components. Component-level from_pretrained still resolves the
            # custom classes through plain importlib.
            print(f"[TripoSG] model_index.json load failed ({exc}); "
                  "constructing pipeline from components.")
            from transformers import BitImageProcessor, Dinov2Model

            vae = TripoSGVAEModel.from_pretrained(
                model_path, subfolder="vae", torch_dtype=dtype
            )
            transformer = TripoSGDiTModel.from_pretrained(
                model_path, subfolder="transformer", torch_dtype=dtype
            )
            scheduler = RectifiedFlowScheduler.from_pretrained(
                model_path, subfolder="scheduler"
            )
            image_encoder = Dinov2Model.from_pretrained(
                model_path, subfolder="image_encoder_dinov2", torch_dtype=dtype
            )
            feature_extractor = BitImageProcessor.from_pretrained(
                model_path, subfolder="feature_extractor_dinov2"
            )

            self._pipe = TripoSGPipeline(
                vae=vae,
                transformer=transformer,
                scheduler=scheduler,
                image_encoder_dinov2=image_encoder,
                feature_extractor_dinov2=feature_extractor,
            ).to(device, dtype)
            print("[TripoSG] Pipeline constructed from components.")

        print(f"[TripoSG] Loading background removal model {settings.RMBG_MODEL_NAME} ...")
        from transformers import AutoModelForImageSegmentation
        self._rmbg = AutoModelForImageSegmentation.from_pretrained(
            settings.RMBG_MODEL_NAME, trust_remote_code=True
        ).to(device)

        self._loaded = True
        print(f"[TripoSG] Loaded on {device}.")
    def unload(self) -> None:
        self._pipe = None
        self._rmbg = None
        self._moge = None
        super().unload()

    # ------------------------------------------------------------------ #
    # Inference
    # ------------------------------------------------------------------ #

    def run(self, image_path: str, params: dict, progress,
            renders_dir: str, output_path: str, cancel_event=None) -> dict:
        import torch
        import trimesh

        if not self.is_loaded():
            self.load()

        fg_ratio = float(params.get("foreground_ratio", 0.85))
        num_steps = int(params.get("num_inference_steps", 50))
        guidance = float(params.get("guidance_scale", 7.0))
        seed = int(params.get("seed", -1))
        faces_target = int(params.get("faces", -1))
        use_flash = str(params.get("use_flash_decoder", "DiffDMC")) == "DiffDMC"

        # ---- Sub-task 1: preprocess (background removal) ----
        progress.update_subtask(1, "Preprocess (Background Removal)", 0, 1)
        image = self._preprocess(image_path, fg_ratio)
        preprocessed_path = str(
            settings.UPLOAD_DIR / f"preprocessed_triposg_{int(time.time() * 1000)}.png"
        )
        image.save(preprocessed_path)
        progress.update_subtask(1, "Preprocess (Background Removal)", 1, 1)
        self._check_cancel(cancel_event)

        # ---- Sub-task 2: flow-matching sampling ----
        if seed < 0:
            seed = int.from_bytes(os.urandom(4), "big") & 0x7FFFFFFF
        device = self._pipe.device

        def step_cb(pipe, i, t, kwargs):
            self._check_cancel(cancel_event)
            progress.update_subtask(2, "Flow-Matching Sampling", i + 1, num_steps)
            return {}

        progress.update_subtask(2, "Flow-Matching Sampling", 0, num_steps)
        generator = torch.Generator(device=device).manual_seed(seed)
        try:
            outputs = self._pipe(
                image=image,
                num_inference_steps=num_steps,
                guidance_scale=guidance,
                generator=generator,
                use_flash_decoder=use_flash,
                callback_on_step_end=step_cb,
            )
        except Exception as exc:
            if use_flash:
                # DiffDMC (diso) can fail at runtime (e.g. kernel/arch mismatch).
                # Fall back to the hierarchical marching-cubes decoder.
                print(f"[TripoSG] Flash decoder failed ({exc}); retrying with Marching Cubes.",
                      flush=True)
                generator = torch.Generator(device=device).manual_seed(seed)
                outputs = self._pipe(
                    image=image,
                    num_inference_steps=num_steps,
                    guidance_scale=guidance,
                    generator=generator,
                    use_flash_decoder=False,
                    callback_on_step_end=step_cb,
                )
            else:
                raise
        self._check_cancel(cancel_event)

        # ---- Sub-task 3: mesh extraction ----
        progress.update_subtask(3, "Mesh Extraction", 0, 1)
        raw = outputs.meshes[0]
        mesh = trimesh.Trimesh(
            vertices=np.asarray(raw.vertices, dtype=np.float32),
            faces=np.asarray(raw.faces, dtype=np.int32),
            process=False,
        )
        mesh.remove_unreferenced_vertices()
        progress.update_subtask(3, "Mesh Extraction", 1, 1)
        self._check_cancel(cancel_event)

        # ---- Sub-task 4: optional simplification ----
        progress.update_subtask(4, "Mesh Simplification", 0, 1)
        if faces_target > 0 and len(mesh.faces) > faces_target:
            mesh = self._simplify(mesh, faces_target)
        progress.update_subtask(4, "Mesh Simplification", 1, 1)
        self._check_cancel(cancel_event)
        # ---- Sub-task 5: texture projection & bake ----
        progress.update_subtask(5, "Texture Projection & Bake", 0, 1)
        vertex_colors = None
        if params.get("enable_texture", True):
            tex_size = int(params.get("texture_size", 1024))
            print(f"[TripoSG] Texturing enabled — baking {tex_size}x{tex_size} atlas.")
            # Project the PREPROCESSED image (background removed, foreground
            # resized) so the mesh surface samples object pixels, not the
            # photo's background.
            mesh, vertex_colors = self._texture_mesh(
                mesh, preprocessed_path, tex_size, fg_ratio=fg_ratio,
            )
        else:
            print("[TripoSG] Texturing disabled (enable_texture=False) — exporting geometry-only mesh.")
        progress.update_subtask(5, "Texture Projection & Bake", 1, 1)

        # ---- Sub-task 6: preview renders ----
        progress.update_subtask(6, "Preview Renders", 0, 1)
        render_paths = self._render_previews(mesh, renders_dir,
                                             base_colors=vertex_colors)
        progress.update_subtask(6, "Preview Renders", 1, 1)
        self._check_cancel(cancel_event)

        # ---- Sub-task 7: GLB export ----
        progress.update_subtask(7, "GLB Export", 0, 1)
        mesh.export(output_path)
        self._log_export_summary(output_path)
        progress.update_subtask(7, "GLB Export", 1, 1)
        return {
            "render_paths": render_paths,
            "state_path": "",
            "preprocessed_image_path": preprocessed_path,
            "camera_angle_x": "",
            "distance": "",
        }

    def _log_export_summary(self, output_path: str) -> None:
        """Log what the exported GLB actually contains (ground-truth check)."""
        try:
            import trimesh
            scene = trimesh.load(output_path, force="scene")
            geoms = scene.geometry.items() if hasattr(scene, "geometry") else [("mesh", scene)]
            for name, geom in geoms:
                vis = getattr(geom, "visual", None)
                mat = getattr(vis, "material", None) if vis is not None else None
                tex = getattr(mat, "baseColorTexture", None) if mat is not None else None
                if tex is not None:
                    a = np.asarray(tex)
                    print(f"[TripoSG] GLB check: {name} — visual={type(vis).__name__}, "
                          f"material={type(mat).__name__}, baseColorTexture={a.shape}, "
                          f"mean={np.round(a.mean(axis=(0, 1)), 1)}")
                else:
                    print(f"[TripoSG] GLB check: {name} — visual={type(vis).__name__ if vis else None}, "
                          f"material={type(mat).__name__ if mat else None}, NO baseColorTexture")
        except Exception as exc:
            print(f"[TripoSG] GLB check failed: {exc}")

    # ------------------------------------------------------------------ #
    # Preprocessing (RMBG-2.0 background removal, like modly's generator)
    # ------------------------------------------------------------------ #
    def _preprocess(self, image_path: str, fg_ratio: float) -> Image.Image:
        import torch
        import torch.nn.functional as F
        from torchvision.transforms.functional import normalize

        image = Image.open(image_path).convert("RGB")
        orig_size = image.size  # (w, h)

        arr = np.array(image)
        im_tensor = torch.tensor(arr, dtype=torch.float32).permute(2, 0, 1)
        im_tensor = F.interpolate(im_tensor.unsqueeze(0), size=(1024, 1024),
                                  mode="bilinear")
        im_tensor = torch.divide(im_tensor, 255.0)
        im_tensor = normalize(im_tensor, [0.5, 0.5, 0.5], [1.0, 1.0, 1.0])

        with torch.no_grad():
            result = self._rmbg(im_tensor.to(self._device))
        logits = result[0][0]
        logits = logits.squeeze()
        if logits.ndim == 3:
            logits = logits[0]
        mask = torch.sigmoid(logits)
        mask = F.interpolate(mask.unsqueeze(0).unsqueeze(0),
                             size=orig_size[::-1], mode="bilinear").squeeze()
        mask_np = (mask.cpu().numpy() > 0.5).astype(np.uint8) * 255

        rgba = image.convert("RGBA")
        rgba.putalpha(Image.fromarray(mask_np))

        # Composite the cut-out subject on a white background.
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[3])
        image = bg.convert("RGB")

        return self._resize_foreground(image, fg_ratio)

    @staticmethod
    def _resize_foreground(image: Image.Image, ratio: float) -> Image.Image:
        """Scale the subject so it occupies `ratio` of the shortest canvas side."""
        arr = np.array(image)
        mask = ~np.all(arr >= 250, axis=-1)
        if not mask.any():
            return image

        rows = np.any(mask, axis=1)
        cols = np.any(mask, axis=0)
        rmin, rmax = np.where(rows)[0][[0, -1]]
        cmin, cmax = np.where(cols)[0][[0, -1]]

        fg = image.crop((cmin, rmin, cmax + 1, rmax + 1))
        fw, fh = fg.size
        iw, ih = image.size
        scale = ratio * min(iw, ih) / max(fw, fh)
        nw = max(1, int(fw * scale))
        nh = max(1, int(fh * scale))
        fg = fg.resize((nw, nh), Image.LANCZOS)

        result = Image.new("RGB", (iw, ih), (255, 255, 255))
        result.paste(fg, ((iw - nw) // 2, (ih - nh) // 2))
        return result

    # ------------------------------------------------------------------ #
    # Mesh helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _simplify(mesh, target_faces: int):
        try:
            import pymeshlab
            import trimesh as _trimesh

            ms = pymeshlab.MeshSet()
            ms.add_mesh(pymeshlab.Mesh(
                vertex_matrix=mesh.vertices,
                face_matrix=mesh.faces,
            ))
            ms.meshing_merge_close_vertices()
            ms.meshing_decimation_quadric_edge_collapse(targetfacenum=target_faces)
            m = ms.current_mesh()
            return _trimesh.Trimesh(vertices=m.vertex_matrix(), faces=m.face_matrix())
        except Exception as exc:
            print(f"[TripoSG] Simplification skipped: {exc}", flush=True)
            return mesh

    # ------------------------------------------------------------------ #
    # Texture projection & bake (input photo -> base-color texture atlas)
    # ------------------------------------------------------------------ #

    def _get_fov(self, image_path: str) -> float:
        """Estimate the horizontal FOV of the input photo with MoGe-2.

        Falls back to a fixed 40 degrees if MoGe is unavailable.
        """
        import torch

        if self._moge is None:
            try:
                from moge.model.v2 import MoGeModel
                self._moge = MoGeModel.from_pretrained(
                    settings.MOGE_MODEL_NAME
                ).to(self._device).eval()
                print("[TripoSG] Loaded MoGe-2 for camera estimation.")
            except Exception as exc:
                print(f"[TripoSG] MoGe-2 unavailable ({exc}); using fixed FOV.")
                return math.radians(40.0)

        try:
            pil = Image.open(image_path).convert("RGB")
            width = pil.width
            arr = np.asarray(pil, dtype=np.float32) / 255.0
            image_t = torch.from_numpy(arr).permute(2, 0, 1).to(self._device)
            with torch.no_grad():
                output = self._moge.infer(image_t)
            intrinsics = output["intrinsics"].squeeze().cpu().numpy()
            fx = float(intrinsics[0, 0]) * width
            fov_x = 2.0 * math.atan(width / (2.0 * fx))
            return float(fov_x)
        except Exception as exc:
            print(f"[TripoSG] FOV estimation failed ({exc}); using fixed FOV.")
            return math.radians(40.0)

    def _texture_mesh(self, mesh, image_path: str, texture_size: int = 1024, fg_ratio: float = 0.85):
        """Project the input photo onto the mesh and bake a base-color atlas.

        Returns (textured_mesh, vertex_colors). The mesh is normalized to the
        unit cube, then the photo is reverse-projected onto the surface from
        four viewpoints (front, back + left/right mirrored), accumulating
        per-vertex colors weighted by rasterized barycentrics. The atlas is
        unwrapped with xatlas and rasterized with nvdiffrast; uncovered
        texels are inpainted. If texturing fails, the raw mesh is returned
        unchanged (geometry-only output).
        """
        import torch

        if not torch.cuda.is_available():
            print("[TripoSG] No CUDA device — skipping texture projection.")
            return mesh, None

        import nvdiffrast.torch as dr
        import cv2

        try:
            # ---- normalize mesh to the unit cube ----
            verts = np.asarray(mesh.vertices, dtype=np.float32)
            faces = np.asarray(mesh.faces, dtype=np.int32)
            if len(verts) == 0 or len(faces) == 0:
                return mesh, None
            lo = verts.min(axis=0)
            hi = verts.max(axis=0)
            center = (lo + hi) / 2.0
            extent = float((hi - lo).max())
            if extent <= 0:
                extent = 1.0
            verts = (verts - center) / extent

            # ---- load the input photo at render resolution (aspect kept) ----
            pil = Image.open(image_path).convert("RGB")
            max_side = 1024
            if max(pil.size) > max_side:
                scale = max_side / max(pil.size)
                pil = pil.resize((int(pil.width * scale), int(pil.height * scale)),
                                 Image.LANCZOS)
            H, W = pil.height, pil.width
            img_t = torch.from_numpy(
                np.asarray(pil, dtype=np.float32) / 255.0
            ).permute(2, 0, 1).unsqueeze(0).to("cuda")          # [1,3,H,W]

            verts_t = torch.from_numpy(verts).cuda()
            faces_t = torch.from_numpy(faces).cuda()
            ctx = dr.RasterizeCudaContext()

            fov_x = self._get_fov(image_path)
            aspect = W / H
            # Frame the mesh so its silhouette fills ~fg_ratio of the frame,
            # matching the foreground coverage of the preprocessed photo.
            dist = (0.5 / math.tan(fov_x / 2.0)) / max(float(fg_ratio), 0.5)
            # Depth bounds cover the normalized [-0.5, 0.5]^3 mesh with margin.
            near = max(1e-3, dist - 1.0)
            far = dist + 1.0
            proj = self._perspective(fov_x, aspect, near, far).cuda()

            vcolor = torch.zeros((len(verts), 3), dtype=torch.float32, device="cuda")
            vcount = torch.zeros((len(verts),), dtype=torch.float32, device="cuda")

            # viewpoint: (yaw_deg, hflip) — front, back (mirrored), left, right
            views = [(0.0, False), (180.0, True), (90.0, True), (-90.0, False)]
            for yaw_deg, hflip in views:
                yaw = math.radians(yaw_deg)
                eye = torch.tensor([
                    dist * math.sin(yaw),
                    0.0,
                    dist * math.cos(yaw),
                ], dtype=torch.float32, device="cuda")
                view = self._lookat(eye, torch.zeros(3, device="cuda"),
                                    torch.tensor([0.0, 1.0, 0.0], device="cuda"))
                pos_h = torch.cat(
                    [verts_t, torch.ones(verts_t.shape[0], 1, device="cuda")],
                    dim=-1,
                )
                pos_clip = (pos_h @ view.T) @ proj.T
                pos_clip = pos_clip[None].contiguous()          # [1,N,4]
                rast, _ = dr.rasterize(ctx, pos_clip, faces_t,
                                       resolution=[H, W])

                # Screen-space color for this view: the photo itself (front),
                # or its horizontal mirror (back/side approximations).
                colors = img_t[0].permute(1, 2, 0)              # [H,W,3]
                if hflip:
                    colors = colors.flip(1)
                colors = colors.unsqueeze(0)                    # [1,H,W,3]

                # nvdiffrast rasterize output is (u, v, z/w, triangle_id):
                #   channel 0,1 = barycentric weights for the 2nd/3rd vertex
                #                 (w0 for the 1st vertex = 1 - u - v)
                #   channel 2   = depth (z/w) — NOT a barycentric
                #   channel 3   = triangle_id, 1-based (0 = empty/background)
                # Reading triangle_id from channel 0 or a barycentric from
                # channel 2 silently white-outs the atlas (the original bug).
                w1 = rast[..., 0:1]                             # u  [1,H,W,1]
                w2 = rast[..., 1:2]                             # v  [1,H,W,1]
                w0 = 1.0 - w1 - w2
                w = torch.cat([w0, w1, w2], dim=-1)             # [1,H,W,3]

                tri_raw = rast[..., 3:4]                        # triangle_id [1,H,W,1]
                valid = (tri_raw > 0.5).squeeze(-1)             # [1,H,W]
                tri_id = (tri_raw[..., 0].long() - 1).clamp(min=0)   # [1,H,W]
                # per-corner vertex ids: [1,H,W,3]
                vids = torch.stack([
                    faces_t[tri_id, 0],
                    faces_t[tri_id, 1],
                    faces_t[tri_id, 2],
                ], dim=-1)                                      # [1,H,W,3]
                for k in range(3):
                    idx = vids[..., k][valid]                  # [P]
                    wt = w[..., k][valid].unsqueeze(-1)        # [P,1]
                    col = colors[valid]                        # [P,3]
                    vcolor.index_add_(0, idx, wt * col)
                    vcount.index_add_(0, idx, wt.squeeze(-1))
            # ---- normalize accumulated colors ----
            covered = vcount > 1e-6
            if covered.any():
                mean_color = (vcolor[covered].sum(dim=0) /
                              covered.sum().float()).clamp(0.0, 1.0)
                vcolor[covered] /= vcount[covered].unsqueeze(-1).clamp(min=1e-6)
                vcolor[~covered] = mean_color
            else:
                vcolor[:] = 0.8
            vcolor = vcolor.clamp(0.0, 1.0)

            # ---- xatlas unwrap (may split vertices along seams) ----
            try:
                import xatlas
                atlas = xatlas.Atlas()
                atlas.add_mesh(verts, faces)
                atlas.generate()
                xm = atlas.get_mesh(0)
                if isinstance(xm, tuple):
                    vmap, faces_out, uvs = xm
                    vmap = np.ascontiguousarray(vmap, dtype=np.int32)
                    faces_out = np.ascontiguousarray(faces_out, dtype=np.int32)
                    uvs = np.ascontiguousarray(uvs, dtype=np.float32)
                else:
                    vmap = np.ascontiguousarray(xm.vertex_map, dtype=np.int32)
                    faces_out = np.ascontiguousarray(xm.face, dtype=np.int32)
                    uvs = np.ascontiguousarray(xm.uv, dtype=np.float32)
            except Exception as exc:
                print(f"[TripoSG] xatlas failed ({exc}); skipping texture bake.")
                return mesh, None

            verts_out = verts[vmap]
            colors_out = vcolor[vmap]

            # Vertex normals for the remapped mesh (GLB lighting needs them).
            import trimesh as _tm
            _tmp = _tm.Trimesh(vertices=verts_out, faces=faces_out, process=False)
            normals_out = _tmp.vertex_normals.astype(np.float32)

            # ---- bake the atlas with nvdiffrast (UV-space rasterization) ----
            # Bake with xatlas's OpenGL-convention UVs (v=0 at bottom); the
            # v-flip for glTF storage is applied to a separate copy below so
            # the bake and the stored UVs each see exactly one flip.
            uv_t = torch.from_numpy(uvs).cuda()
            faces_out_t = torch.from_numpy(faces_out).cuda()
            colors_out_t = colors_out.clone()
            # UVs -> clip space
            uv_clip = torch.cat(
                [uv_t * 2.0 - 1.0, torch.zeros_like(uv_t[:, :1]),
                 torch.ones_like(uv_t[:, :1])], dim=-1
            )[None].contiguous()                               # [1,N,4]
            rast_uv, _ = dr.rasterize(ctx, uv_clip, faces_out_t,
                                      resolution=[texture_size, texture_size])
            col_uv, _ = dr.interpolate(
                colors_out_t[None], rast_uv, faces_out_t
            )                                                  # [1,TS,TS,3]
            atlas_img = col_uv[0].cpu().numpy()                # [TS,TS,3]
            # triangle_id lives in channel 3 (1-based; 0 = empty) — NOT
            # channel 0, which is the barycentric u coordinate.
            mask = (rast_uv[0, ..., 3].cpu().numpy() > 0.5)    # covered texels

            # ---- inpaint uncovered texels ----
            atlas_u8 = np.clip(atlas_img * 255.0, 0, 255).astype(np.uint8)
            mask_u8 = (~mask).astype(np.uint8) * 255
            if mask_u8.any():
                atlas_u8 = cv2.inpaint(atlas_u8, mask_u8, 3, cv2.INPAINT_TELEA)

            # ---- build the textured trimesh ----
            # glTF UV origin is top-left; flip v on the stored copy only so
            # the baked atlas (rasterized bottom-up) maps correctly.
            uvs_store = uvs.copy()
            uvs_store[:, 1] = 1.0 - uvs_store[:, 1]
            import trimesh
            material = trimesh.visual.material.PBRMaterial(
                baseColorTexture=Image.fromarray(atlas_u8),
                baseColorFactor=np.array([255, 255, 255, 255], dtype=np.uint8),
                roughnessFactor=0.6,
                metallicFactor=0.0,
                alphaMode='OPAQUE',
                doubleSided=True,
            )
            textured = trimesh.Trimesh(
                vertices=verts_out,
                faces=faces_out,
                vertex_normals=normals_out,
                process=False,
                visual=trimesh.visual.TextureVisuals(
                    uv=uvs_store, material=material,
                ),
            )
            print(f"[TripoSG] Texture baked: {texture_size}x{texture_size} atlas, "
                  f"{len(verts_out)} verts.")
            # per-vertex colors in the REMAPPED mesh ordering (for previews)
            return textured, colors_out

        except Exception as exc:
            print(f"[TripoSG] Texture projection failed ({exc}); "
                  "exporting geometry-only mesh.")
            return mesh, None

    # ------------------------------------------------------------------ #
    # Preview renders (nvdiffrast turntable, no OpenGL context needed)
    # ------------------------------------------------------------------ #

    def _render_previews(self, mesh, renders_dir: str,
                         num_frames: int = 8, resolution: int = 512,
                         base_colors=None) -> dict:
        import torch

        if not torch.cuda.is_available():
            # nvdiffrast's CUDA rasterizer is GPU-only; the app targets GPU
            # workers, but degrade gracefully instead of failing the task.
            print("[TripoSG] No CUDA device — skipping preview renders.")
            return {}

        import nvdiffrast.torch as dr

        verts = np.asarray(mesh.vertices, dtype=np.float32)
        faces = np.asarray(mesh.faces, dtype=np.int32)
        if len(verts) == 0 or len(faces) == 0:
            return {}

        # Normalize to a unit cube centered at the origin.
        lo = verts.min(axis=0)
        hi = verts.max(axis=0)
        center = (lo + hi) / 2.0
        extent = float((hi - lo).max())
        if extent <= 0:
            extent = 1.0
        verts = (verts - center) / extent

        normals = np.asarray(mesh.vertex_normals, dtype=np.float32)
        if normals.shape != verts.shape:
            import trimesh as _tm
            normals = _tm.Trimesh(
                vertices=verts, faces=faces, process=False
            ).vertex_normals.astype(np.float32)

        verts_t = torch.from_numpy(verts).cuda()
        faces_t = torch.from_numpy(faces).cuda()
        normals_t = torch.from_numpy(normals).cuda()
        ctx = dr.RasterizeCudaContext()

        fov = math.radians(60.0)
        dist = 2.2
        # Depth bounds cover the normalized [-0.5, 0.5]^3 mesh with margin.
        near = max(1e-3, dist - 1.0)
        far = dist + 1.0
        elevation = math.radians(15.0)

        light = torch.tensor([0.45, 0.6, 1.0], dtype=torch.float32, device="cuda")
        light = light / light.norm()
        base_color = torch.tensor([0.78, 0.80, 0.84], dtype=torch.float32, device="cuda")
        proj = self._perspective(fov, 1.0, near, far).cuda()

        files = []
        for i in range(num_frames):
            az = 2.0 * math.pi * i / num_frames
            eye = torch.tensor([
                dist * math.cos(elevation) * math.cos(az),
                dist * math.sin(elevation),
                dist * math.cos(elevation) * math.sin(az),
            ], dtype=torch.float32, device="cuda")
            view = self._lookat(eye, torch.zeros(3, device="cuda"),
                                torch.tensor([0.0, 1.0, 0.0], device="cuda"))

            # nvdiffrast 0.4 dropped the transform_pos helper — apply the
            # matrices manually. Each ROW of m holds the output basis +
            # translation, applied as clip = [pos, 1] @ m.T.
            pos_h = torch.cat(
                [verts_t, torch.ones(verts_t.shape[0], 1, device=verts_t.device)],
                dim=-1,
            )
            pos_cam = pos_h @ view.T      # [N, 4]
            pos_clip = pos_cam @ proj.T   # [N, 4]
            # rasterize/interpolate/antialias require contiguous CUDA tensors.
            pos_clip = pos_clip[None].contiguous()   # [1, N, 4]
            rast, _ = dr.rasterize(ctx, pos_clip, faces_t,
                                   resolution=[resolution, resolution])

            lambert = torch.clamp(normals_t @ light, min=0.0, max=1.0)
            shade = 0.35 + 0.65 * lambert
            if base_colors is not None:
                bc = base_colors if torch.is_tensor(base_colors) else torch.from_numpy(
                    np.asarray(base_colors, dtype=np.float32)).to(verts_t.device)
                rgb = (shade.unsqueeze(1) * bc).unsqueeze(0)              # [1,N,3]
            else:
                rgb = (shade.unsqueeze(1) * base_color).unsqueeze(0)      # [1,N,3]
            rgba = torch.cat(
                [rgb, torch.ones(1, rgb.shape[1], 1, device="cuda")], dim=-1
            )                                                              # [1,N,4]
            col, _ = dr.interpolate(rgba, rast, faces_t)                  # [1,H,W,4]

            img = dr.antialias(col, rast, pos_clip, faces_t)[0]           # [H,W,4]
            img = img[..., :3] + (1.0 - img[..., 3:4])
            arr = (255.0 * torch.clamp(img, 0.0, 1.0)).cpu().numpy().astype(np.uint8)

            path = os.path.abspath(os.path.join(
                renders_dir, f"render_preview_{i}_{int(time.time() * 1000)}.jpg"
            ))
            Image.fromarray(arr).save(path, quality=85)
            files.append(path)

        return {"preview": files}

    @staticmethod
    def _lookat(eye: "torch.Tensor", target: "torch.Tensor", up: "torch.Tensor"):
        """World->camera view matrix (classic gluLookAt, row-major).

        Applied as clip = [pos, 1] @ m.T, so each ROW of m holds the
        output basis + translation. Visible points end up with negative
        camera-space z (OpenGL convention).
        """
        import torch
        f = target - eye          # camera forward (toward the target)
        f = f / f.norm()
        s = torch.linalg.cross(f, up)   # camera right
        s = s / s.norm()
        u = torch.linalg.cross(s, f)    # camera up
        m = torch.zeros((4, 4), dtype=torch.float32, device=eye.device)
        m[0, 0], m[0, 1], m[0, 2] = s[0], s[1], s[2]
        m[1, 0], m[1, 1], m[1, 2] = u[0], u[1], u[2]
        m[2, 0], m[2, 1], m[2, 2] = -f[0], -f[1], -f[2]
        m[0, 3] = -torch.dot(s, eye)
        m[1, 3] = -torch.dot(u, eye)
        m[2, 3] = torch.dot(f, eye)
        m[3, 3] = 1.0
        return m

    @staticmethod
    def _perspective(fovy: float, aspect: float, near: float, far: float):
        """OpenGL perspective matrix (row-major, [pos, 1] @ m.T)."""
        import torch
        f = 1.0 / math.tan(fovy / 2.0)
        m = torch.zeros((4, 4), dtype=torch.float32)
        m[0, 0] = f / aspect
        m[1, 1] = f
        m[2, 2] = (far + near) / (near - far)
        m[2, 3] = (2.0 * far * near) / (near - far)
        m[3, 2] = -1.0
        return m
