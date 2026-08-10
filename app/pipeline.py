import os
import math
import time
import threading
import numpy as np
import torch
from PIL import Image
from typing import Any, Optional

os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
os.environ.setdefault("FLEX_GEMM_AUTOTUNE_CACHE_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "autotune_cache.json"))
os.environ.setdefault("FLEX_GEMM_AUTOTUNER_VERBOSE", "1")

from app.config import settings

if settings.HF_HOME:
    os.environ["HF_HOME"] = settings.HF_HOME

_init_lock = threading.Lock()
_pipeline = None
_moge_model = None


class CancelledError(Exception):
    """Raised when a task is cancelled between sub-tasks."""
    pass

IMAGE_COND_CONFIGS = {
    "ss": {
        "model_name": "camenduru/dinov3-vitl16-pretrain-lvd1689m",
        "image_size": 512,
        "grid_resolution": 16,
    },
    "shape_512": {
        "model_name": "camenduru/dinov3-vitl16-pretrain-lvd1689m",
        "image_size": 512,
        "grid_resolution": 32,
        "use_naf_upsample": True,
        "naf_target_size": 512,
    },
    "shape_1024": {
        "model_name": "camenduru/dinov3-vitl16-pretrain-lvd1689m",
        "image_size": 1024,
        "grid_resolution": 64,
        "use_naf_upsample": True,
        "naf_target_size": 512,
    },
    "tex_1024": {
        "model_name": "camenduru/dinov3-vitl16-pretrain-lvd1689m",
        "image_size": 1024,
        "grid_resolution": 64,
        "use_naf_upsample": True,
        "naf_target_size": 1024,
    },
}

CASCADE_MAX_NUM_TOKENS = 49152


def _safe_simplify(mesh, target, timeout_sec=120):
    """Run CuMesh simplify with a thread-based timeout. Falls back to CPU
    trimesh decimation if CuMesh hangs (common on Blackwell GPUs)."""
    result = {"error": None, "done": False}

    def _do():
        try:
            mesh.simplify(target, verbose=True)
            result["done"] = True
        except Exception as e:
            result["error"] = e

    t = threading.Thread(target=_do, daemon=True)
    t.start()
    t.join(timeout=timeout_sec)

    if not result["done"]:
        if result["error"]:
            raise result["error"]
        raise TimeoutError(f"CuMesh simplify(target={target}) timed out after {timeout_sec}s")


def _import_cls(module_path: str, cls_name: str):
    import importlib
    mod = importlib.import_module(module_path)
    return getattr(mod, cls_name)


def _build_image_cond_model(config: dict):
    try:
        DinoV3ProjFeatureExtractor = _import_cls(
            "pixal3d.trainers.flow_matching.mixins.image_conditioned_proj",
            "DinoV3ProjFeatureExtractor",
        )
    except ImportError:
        DinoV3ProjFeatureExtractor = _import_cls(
            "trellis2.trainers.flow_matching.mixins.image_conditioned_proj",
            "DinoV3ProjFeatureExtractor",
        )
    model = DinoV3ProjFeatureExtractor(**config)
    model.eval()
    return model


def _load_moge_model(device="cuda"):
    from moge.model.v2 import MoGeModel
    model = MoGeModel.from_pretrained(settings.MOGE_MODEL_NAME).to(device)
    model.eval()
    return model


def init_pipeline():
    """Load the Pixal3D pipeline + MoGe-2 onto the GPU.

    Each worker process sets CUDA_VISIBLE_DEVICES so only one GPU is visible
    (as cuda:0). This function is called once per worker process.
    """
    global _pipeline, _moge_model
    with _init_lock:
        if _pipeline is not None:
            return

        os.environ["ATTN_BACKEND"] = settings.ATTN_BACKEND

        try:
            from pixal3d.pipelines import Pixal3DImageTo3DPipeline
        except ImportError:
            from trellis2.pipelines import Pixal3DImageTo3DPipeline

        print(f"[Pipeline] Loading from {settings.PIXAL3D_MODEL_PATH}...")
        _pipeline = Pixal3DImageTo3DPipeline.from_pretrained(settings.PIXAL3D_MODEL_PATH)

        print("[ImageCond] Building DinoV3ProjFeatureExtractor models...")
        _pipeline.image_cond_model_ss = _build_image_cond_model(IMAGE_COND_CONFIGS["ss"])
        _pipeline.image_cond_model_shape_512 = _build_image_cond_model(IMAGE_COND_CONFIGS["shape_512"])
        _pipeline.image_cond_model_shape_1024 = _build_image_cond_model(IMAGE_COND_CONFIGS["shape_1024"])
        _pipeline.image_cond_model_tex_1024 = _build_image_cond_model(IMAGE_COND_CONFIGS["tex_1024"])

        if settings.LOW_VRAM:
            for attr in ['image_cond_model_ss', 'image_cond_model_shape_512',
                         'image_cond_model_shape_1024', 'image_cond_model_tex_1024']:
                m = getattr(_pipeline, attr, None)
                if m is not None and getattr(m, 'use_naf_upsample', False):
                    m._load_naf()
            _pipeline._device = torch.device("cuda")
            _pipeline.low_vram = True
            print("[Pipeline] Low-VRAM mode enabled.")
        else:
            _pipeline.low_vram = False
            _pipeline.cuda()
            _pipeline.image_cond_model_ss.cuda()
            _pipeline.image_cond_model_shape_512.cuda()
            _pipeline.image_cond_model_shape_1024.cuda()
            _pipeline.image_cond_model_tex_1024.cuda()
            _pipeline._device = torch.device("cuda")
            for attr in ['image_cond_model_ss', 'image_cond_model_shape_512',
                         'image_cond_model_shape_1024', 'image_cond_model_tex_1024']:
                m = getattr(_pipeline, attr, None)
                if m is not None and getattr(m, 'use_naf_upsample', False):
                    m._load_naf()
            print("[Pipeline] Standard mode (all models on GPU).")

        print("[MoGe-2] Loading model for camera estimation...")
        _moge_model = _load_moge_model(device="cuda")


def _compute_f_pixels(camera_angle_x: float, resolution: int) -> float:
    focal_length = 16.0 / torch.tan(torch.tensor(camera_angle_x / 2.0))
    f_pixels = focal_length * resolution / 32.0
    return float(f_pixels.item())


def _distance_from_fov(camera_angle_x, grid_point, target_point, mesh_scale, image_resolution):
    rotation_matrix = torch.tensor([[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]])
    gp = grid_point.to(torch.float32) @ rotation_matrix.T
    gp = gp / mesh_scale / 2
    xw, yw, zw = gp[0].item(), gp[1].item(), gp[2].item()
    xt, yt = float(target_point[0].item()), float(target_point[1].item())
    f_pixels = _compute_f_pixels(camera_angle_x, image_resolution)
    x_ndc = xt - image_resolution / 2.0
    y_ndc = -(yt - image_resolution / 2.0)
    distance_x = f_pixels * xw / x_ndc - yw
    return {"distance_from_x": float(distance_x), "f_pixels": float(f_pixels)}


def _get_camera_params(image_path, mesh_scale=1.0, extend_pixel=0, image_resolution=512):
    init_pipeline()
    pil_image = Image.open(image_path).convert("RGB")
    width, height = pil_image.size
    image_np = np.array(pil_image).astype(np.float32) / 255.0
    image_tensor = torch.from_numpy(image_np).permute(2, 0, 1).to("cuda")
    with torch.no_grad():
        output = _moge_model.infer(image_tensor)
    intrinsics = output["intrinsics"].squeeze().cpu().numpy()
    fx_normalized = intrinsics[0, 0]
    fx = fx_normalized * width
    camera_angle_x = 2 * math.atan(width / (2 * fx))

    grid_point = torch.tensor([-1.0, 0.0, 0.0])
    distance = _distance_from_fov(
        camera_angle_x, grid_point,
        torch.tensor([0 - extend_pixel, image_resolution - 1 + extend_pixel]),
        mesh_scale, image_resolution
    )["distance_from_x"]
    return {'camera_angle_x': camera_angle_x, 'distance': distance, 'mesh_scale': mesh_scale}


class ProgressCallback:
    def __init__(self):
        self.subtask_index = 0
        self.subtask_name = ""
        self.subtask_step = 0
        self.subtask_total_steps = 0
        # Legacy single progress fields (kept for compatibility / DB columns
        # progress_step / progress_total which we still mirror).
        self.stage = ""
        self.step = 0
        self.total = 0

    def update_subtask(self, subtask_index: int, subtask_name: str,
                       subtask_step: int, subtask_total_steps: int):
        self.subtask_index = subtask_index
        self.subtask_name = subtask_name
        self.subtask_step = subtask_step
        self.subtask_total_steps = subtask_total_steps
        # Mirror legacy fields
        self.stage = subtask_name
        self.step = subtask_step
        self.total = subtask_total_steps

    def update(self, stage: str, step: int, total: int):
        self.stage = stage
        self.step = step
        self.total = total


def preprocess_image(image_path: str) -> str:
    init_pipeline()
    img = Image.open(image_path)
    processed = _pipeline.preprocess_image(img)
    out_path = str(settings.UPLOAD_DIR / f"preprocessed_{int(time.time()*1000)}.png")
    processed.save(out_path)
    return out_path


def generate_3d(
    preprocessed_image_path: str,
    params: dict,
    progress: ProgressCallback,
    renders_dir: str,
    cancel_event: Optional[threading.Event] = None,
) -> dict:
    import o_voxel
    try:
        from pixal3d.modules.sparse import SparseTensor
        from pixal3d.utils import render_utils
        from pixal3d.renderers import EnvMap
    except ImportError:
        from trellis2.modules.sparse import SparseTensor
        from trellis2.utils import render_utils
        from trellis2.renderers import EnvMap
    import cv2

    init_pipeline()

    def _check_cancel():
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError("Task cancelled")

    torch.manual_seed(params["seed"])
    hr_resolution = int(params["resolution"])

    img = Image.open(preprocessed_image_path)
    image_preprocessed = img

    # ---- Sub-task 1: Preprocessing & Camera Estimation ----
    progress.update_subtask(1, "Preprocessing & Camera Estimation", 0, 1)

    manual_fov = params.get("manual_fov", -1.0)
    fov_unit = params.get("fov_unit", "deg")
    mesh_scale = params.get("mesh_scale", 1.0)
    extend_pixel = params.get("extend_pixel", 0)
    image_resolution = params.get("image_resolution", 512)

    if manual_fov > 0:
        if fov_unit == "rad":
            camera_angle_x = float(manual_fov)
        else:
            camera_angle_x = math.radians(float(manual_fov))
        grid_point = torch.tensor([-1.0, 0.0, 0.0])
        distance = _distance_from_fov(
            camera_angle_x, grid_point,
            torch.tensor([0 - extend_pixel, image_resolution - 1 + extend_pixel]),
            mesh_scale, image_resolution
        )["distance_from_x"]
        camera_params = {'camera_angle_x': camera_angle_x, 'distance': distance, 'mesh_scale': mesh_scale}
    else:
        camera_params = _get_camera_params(
            preprocessed_image_path,
            mesh_scale=mesh_scale, extend_pixel=extend_pixel,
            image_resolution=image_resolution,
        )

    progress.update_subtask(1, "Preprocessing & Camera Estimation", 1, 1)
    _check_cancel()

    ss_sampler_override = {
        "steps": params["ss_sampling_steps"],
        "guidance_strength": params["ss_guidance_strength"],
        "guidance_rescale": params["ss_guidance_rescale"],
        "rescale_t": params["ss_rescale_t"],
    }
    shape_sampler_override = {
        "steps": params["shape_slat_sampling_steps"],
        "guidance_strength": params["shape_slat_guidance_strength"],
        "guidance_rescale": params["shape_slat_guidance_rescale"],
        "rescale_t": params["shape_slat_rescale_t"],
    }
    tex_sampler_override = {
        "steps": params["tex_slat_sampling_steps"],
        "guidance_strength": params["tex_slat_guidance_strength"],
        "guidance_rescale": params["tex_slat_guidance_rescale"],
        "rescale_t": params["tex_slat_rescale_t"],
    }

    pipeline_type = f"{hr_resolution}_cascade"

    # ---- Sub-task 2/3/4: Sparse Structure, Shape, Texture ----
    # We cannot easily intercept the internal per-stage step callbacks of the
    # monolithic _pipeline.run(), so we report the three stages with an
    # indeterminate within-subtask bar (0/0) while the call is in flight, and
    # mark each complete right after. The overall progress still advances by
    # the stage weights as each completes.
    progress.update_subtask(2, "Stage 1: Sparse Structure", 0, params["ss_sampling_steps"])
    mesh_list, (shape_slat, tex_slat, res) = _pipeline.run(
        image_preprocessed,
        camera_params=camera_params,
        seed=params["seed"],
        sparse_structure_sampler_params=ss_sampler_override,
        shape_slat_sampler_params=shape_sampler_override,
        tex_slat_sampler_params=tex_sampler_override,
        preprocess_image=False,
        return_latent=True,
        pipeline_type=pipeline_type,
        max_num_tokens=params.get("max_num_tokens", CASCADE_MAX_NUM_TOKENS),
    )

    mesh = mesh_list[0]
    _check_cancel()

    state_data = {
        'shape_slat_feats': shape_slat.feats.cpu().numpy(),
        'tex_slat_feats': tex_slat.feats.cpu().numpy(),
        'coords': shape_slat.coords.cpu().numpy(),
        'res': res,
    }
    state_path = os.path.join(renders_dir, f"state_{int(time.time()*1000)}.npz")
    np.savez_compressed(state_path, **state_data)

    # ---- Sub-task 5: Rendering preview views ----
    progress.update_subtask(5, "Rendering preview views", 0, 1)
    _safe_simplify(mesh, 16777216, timeout_sec=60)
    cam_dist = camera_params['distance']
    near = max(0.01, cam_dist - 2.0)
    far = cam_dist + 10.0

    _base = os.path.dirname(os.path.abspath(__file__))
    hdri_dir = os.path.join(_base, "..", "assets", "hdri")
    envmap = {}
    for name in ["forest", "sunset", "courtyard"]:
        hdri_path = os.path.join(hdri_dir, f"{name}.exr")
        if os.path.exists(hdri_path):
            envmap[name] = EnvMap(torch.tensor(
                cv2.cvtColor(cv2.imread(hdri_path, cv2.IMREAD_UNCHANGED), cv2.COLOR_BGR2RGB),
                dtype=torch.float32, device='cuda'
            ))

    renders = render_utils.render_proj_aligned_video(
        mesh, camera_angle_x=camera_params['camera_angle_x'],
        distance=cam_dist, resolution=1024,
        num_frames=8, envmap=envmap if envmap else None,
        near=near, far=far,
    )
    progress.update_subtask(5, "Rendering preview views", 1, 1)
    _check_cancel()

    render_files = {}
    for mode_key, frames in renders.items():
        mode_files = []
        for i, frame in enumerate(frames):
            p = os.path.abspath(os.path.join(renders_dir, f"render_{mode_key}_{i}_{int(time.time()*1000)}.jpg"))
            Image.fromarray(frame).save(p, quality=85)
            mode_files.append(p)
        render_files[mode_key] = mode_files

    return {
        "render_paths": render_files,
        "state_path": os.path.abspath(state_path),
        "camera_angle_x": camera_params['camera_angle_x'],
        "distance": camera_params['distance'],
    }


def extract_glb(
    state_path: str,
    decimation_target: int,
    texture_size: int,
    progress: ProgressCallback,
    output_path: str,
    gpu_id: int = 0,
    cancel_event: Optional[threading.Event] = None,
):
    import o_voxel
    try:
        from pixal3d.modules.sparse import SparseTensor
    except ImportError:
        from trellis2.modules.sparse import SparseTensor

    init_pipeline()

    def _check_cancel():
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError("Task cancelled")

    # GLB extraction has 12 internal steps; we report them as the within-subtask
    # progress for sub-task 6.
    GLB_STEPS = 12

    def _glb_progress(step: int):
        progress.update_subtask(6, "GLB Extraction", step, GLB_STEPS)
        _check_cancel()

    print("[GLB] Decoding latent...", flush=True)
    _glb_progress(0)
    data = np.load(state_path)
    shape_slat = SparseTensor(
        feats=torch.from_numpy(data['shape_slat_feats']).to('cuda'),
        coords=torch.from_numpy(data['coords']).to('cuda'),
    )
    tex_slat = shape_slat.replace(torch.from_numpy(data['tex_slat_feats']).to('cuda'))
    res = int(data['res'])

    print(f"[GLB] decode_latent(res={res})...", flush=True)
    mesh_decode = _pipeline.decode_latent(shape_slat, tex_slat, res)[0]
    print(f"[GLB] Mesh decoded: {len(mesh_decode.vertices)} verts, {len(mesh_decode.faces)} faces", flush=True)
    _glb_progress(1)

    print(f"[GLB] to_glb(decimation={decimation_target}, texture={texture_size}, remesh=False)...", flush=True)

    # Manual step-by-step GLB extraction for debugging
    import cumesh
    import nvdiffrast.torch as dr
    from flex_gemm.ops.grid_sample import grid_sample_3d
    import trimesh
    import trimesh.visual
    import cv2

    aabb_t = torch.tensor([[-0.5,-0.5,-0.5],[0.5,0.5,0.5]], dtype=torch.float32, device='cuda')
    gs = torch.tensor([res,res,res], dtype=torch.int32, device='cuda')
    voxel_size = (aabb_t[1] - aabb_t[0]) / gs

    print("[GLB] Step 1: init CuMesh...", flush=True)
    mesh = cumesh.CuMesh()
    mesh.init(vertices=mesh_decode.vertices, faces=mesh_decode.faces)
    _glb_progress(2)

    print(f"[GLB] Step 2: fill_holes ({mesh.num_vertices} verts, {mesh.num_faces} faces)...", flush=True)
    mesh.fill_holes(max_hole_perimeter=3e-2)
    print(f"[GLB]   -> {mesh.num_vertices} verts, {mesh.num_faces} faces", flush=True)
    _glb_progress(3)

    print("[GLB] Step 3: build BVH...", flush=True)
    v2, f2 = mesh.read()
    bvh = cumesh.cuBVH(v2, f2)
    print("[GLB]   BVH done", flush=True)
    _glb_progress(4)

    print("[GLB] Step 4: simplify (3x target)...", flush=True)
    _safe_simplify(mesh, decimation_target * 3)
    print(f"[GLB]   -> {mesh.num_vertices} verts, {mesh.num_faces} faces", flush=True)
    _glb_progress(5)

    print("[GLB] Step 5: cleanup topology...", flush=True)
    mesh.remove_duplicate_faces()
    mesh.repair_non_manifold_edges()
    mesh.remove_small_connected_components(1e-5)
    mesh.fill_holes(max_hole_perimeter=3e-2)
    print(f"[GLB]   -> {mesh.num_vertices} verts, {mesh.num_faces} faces", flush=True)
    _glb_progress(6)

    print("[GLB] Step 6: simplify (target)...", flush=True)
    _safe_simplify(mesh, decimation_target)
    print(f"[GLB]   -> {mesh.num_vertices} verts, {mesh.num_faces} faces", flush=True)
    _glb_progress(7)

    print("[GLB] Step 7: final cleanup...", flush=True)
    mesh.remove_duplicate_faces()
    mesh.repair_non_manifold_edges()
    mesh.remove_small_connected_components(1e-5)
    mesh.fill_holes(max_hole_perimeter=3e-2)
    mesh.unify_face_orientations()
    print(f"[GLB]   -> {mesh.num_vertices} verts, {mesh.num_faces} faces", flush=True)
    _glb_progress(8)

    print("[GLB] Step 8: UV unwrap...", flush=True)
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
    out_vertices = out_vertices.to('cuda')
    out_faces = out_faces.to('cuda')
    out_uvs = out_uvs.to('cuda')
    out_vmaps = out_vmaps.to('cuda')
    mesh.compute_vertex_normals()
    out_normals = mesh.read_vertex_normals()[out_vmaps]
    print(f"[GLB]   UV done: {out_vertices.shape[0]} verts, {out_faces.shape[0]} faces", flush=True)
    _glb_progress(9)

    print("[GLB] Step 9: texture baking...", flush=True)
    ctx = dr.RasterizeCudaContext()
    uvs_rast = torch.cat([out_uvs * 2 - 1, torch.zeros_like(out_uvs[:, :1]), torch.ones_like(out_uvs[:, :1])], dim=-1).unsqueeze(0)
    rast = torch.zeros((1, texture_size, texture_size, 4), device='cuda', dtype=torch.float32)
    for i in range(0, out_faces.shape[0], 100000):
        rast_chunk, _ = dr.rasterize(ctx, uvs_rast, out_faces[i:i+100000], resolution=[texture_size, texture_size])
        mask_chunk = rast_chunk[..., 3:4] > 0
        rast_chunk[..., 3:4] += i
        rast = torch.where(mask_chunk, rast_chunk, rast)
    mask = rast[0, ..., 3] > 0
    pos = dr.interpolate(out_vertices.unsqueeze(0), rast, out_faces)[0][0]
    valid_pos = pos[mask]
    _, face_id, uvw = bvh.unsigned_distance(valid_pos, return_uvw=True)
    orig_tri_verts = v2[f2[face_id.long()]]
    valid_pos = (orig_tri_verts * uvw.unsqueeze(-1)).sum(dim=1)
    attrs = torch.zeros(texture_size, texture_size, mesh_decode.attrs.shape[1], device='cuda')
    sampled = grid_sample_3d(
        mesh_decode.attrs,
        torch.cat([torch.zeros_like(mesh_decode.coords[:, :1]), mesh_decode.coords], dim=-1),
        shape=torch.Size([1, mesh_decode.attrs.shape[1], res, res, res]),
        grid=((valid_pos - aabb_t[0]) / voxel_size).reshape(1, -1, 3),
        mode='trilinear',
    )
    M = valid_pos.shape[0]
    C = mesh_decode.attrs.shape[1]
    if sampled.shape != (M, C):
        if sampled.numel() == M * C:
            sampled = sampled.reshape(M, C)
        elif sampled.dim() == 3 and sampled.shape[0] == texture_size and sampled.shape[1] == texture_size:
            sampled = sampled[mask]
        else:
            sampled = sampled.reshape(M, C)
    attrs[mask] = sampled
    print("[GLB]   texture baked", flush=True)
    _glb_progress(10)

    print("[GLB] Step 10: finalize texture...", flush=True)
    attr_layout = _pipeline.pbr_attr_layout
    mask_np = mask.cpu().numpy()
    base_color = np.clip(attrs[..., attr_layout['base_color']].cpu().numpy() * 255, 0, 255).astype(np.uint8)
    metallic = np.clip(attrs[..., attr_layout['metallic']].cpu().numpy() * 255, 0, 255).astype(np.uint8)
    roughness = np.clip(attrs[..., attr_layout['roughness']].cpu().numpy() * 255, 0, 255).astype(np.uint8)
    alpha = np.clip(attrs[..., attr_layout['alpha']].cpu().numpy() * 255, 0, 255).astype(np.uint8)
    mask_inv = (~mask_np).astype(np.uint8)
    base_color = cv2.inpaint(base_color, mask_inv, 3, cv2.INPAINT_TELEA)
    metallic = cv2.inpaint(metallic, mask_inv, 1, cv2.INPAINT_TELEA)[..., None]
    roughness = cv2.inpaint(roughness, mask_inv, 1, cv2.INPAINT_TELEA)[..., None]
    alpha = cv2.inpaint(alpha, mask_inv, 1, cv2.INPAINT_TELEA)[..., None]
    material = trimesh.visual.material.PBRMaterial(
        baseColorTexture=Image.fromarray(np.concatenate([base_color, alpha], axis=-1)),
        baseColorFactor=np.array([255, 255, 255, 255], dtype=np.uint8),
        metallicRoughnessTexture=Image.fromarray(np.concatenate([np.zeros_like(metallic), roughness, metallic], axis=-1)),
        metallicFactor=1.0, roughnessFactor=1.0, alphaMode='OPAQUE', doubleSided=True,
    )

    print("[GLB] Step 11: build trimesh...", flush=True)
    vertices_np = out_vertices.cpu().numpy()
    faces_np = out_faces.cpu().numpy()
    uvs_np = out_uvs.cpu().numpy()
    normals_np = out_normals.cpu().numpy()
    vertices_np[:, 1], vertices_np[:, 2] = vertices_np[:, 2], -vertices_np[:, 1]
    normals_np[:, 1], normals_np[:, 2] = normals_np[:, 2], -normals_np[:, 1]
    uvs_np[:, 1] = 1 - uvs_np[:, 1]
    glb = trimesh.Trimesh(
        vertices=vertices_np, faces=faces_np, vertex_normals=normals_np,
        process=False, visual=trimesh.visual.TextureVisuals(uv=uvs_np, material=material)
    )
    _glb_progress(11)

    print(f"[GLB] Step 12: export to {output_path}...", flush=True)
    rot = np.array([[-1,0,0,0],[0,0,-1,0],[0,-1,0,0],[0,0,0,1]], dtype=np.float64)
    glb.apply_transform(rot)
    glb.export(output_path, extension_webp=True)
    print("[GLB] Done!", flush=True)
    _glb_progress(12)
