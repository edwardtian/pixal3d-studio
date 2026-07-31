PARAMETER_DEFINITIONS = {
    "seed": {
        "label": "Generation Seed",
        "type": "int",
        "default": 42,
        "min": 0,
        "max": 2147483647,
        "step": 1,
        "group": "Base",
        "tooltip": "Random seed controlling the entire generation process. Same seed + same image + same params = identical output. Change the seed to explore different 3D variations from the same image.",
    },
    "resolution": {
        "label": "Target Resolution",
        "type": "select",
        "default": 1536,
        "options": [
            {"value": 1024, "label": "1024 (Balanced)"},
            {"value": 1536, "label": "1536 (High Quality)"},
        ],
        "group": "Base",
        "tooltip": "Internal pipeline resolution. 1536 produces higher-fidelity geometry and textures but requires more VRAM and time. 1024 is faster and uses less memory. In low-VRAM mode the default is 1024.",
    },
    "manual_fov": {
        "label": "Camera FOV (Field of View)",
        "type": "float",
        "default": -1.0,
        "min": -1.0,
        "max": 170.0,
        "step": 0.5,
        "group": "Base",
        "tooltip": "Camera field-of-view in degrees. Set to -1 (Auto) to let MoGe-2 estimate the FOV from the image automatically. If the generated 3D model looks distorted or stretched, try a manual value — 11.5° (≈0.2 rad) generally works well for most object-centric images.",
    },
    "fov_unit": {
        "label": "FOV Unit",
        "type": "select",
        "default": "deg",
        "options": [
            {"value": "deg", "label": "Degrees"},
            {"value": "rad", "label": "Radians"},
        ],
        "group": "Base",
        "tooltip": "Unit for the manual FOV value. Degrees are more intuitive (1°–170°); radians are the raw mathematical unit (0.02–2.97 rad). Only used when manual FOV is enabled.",
    },
    "low_vram": {
        "label": "Low VRAM Mode",
        "type": "bool",
        "default": False,
        "group": "Base",
        "tooltip": "When enabled, models are kept on CPU and loaded to GPU on-demand per pipeline stage. Reduces peak VRAM from ~18 GB to ~10–12 GB at the cost of slower inference due to repeated CPU↔GPU transfers.",
    },

    "ss_guidance_strength": {
        "label": "SS Guidance Strength",
        "type": "float",
        "default": 7.5,
        "min": 1.0,
        "max": 10.0,
        "step": 0.1,
        "group": "Sparse Structure",
        "tooltip": "Classifier-free guidance scale for the Sparse Structure (Stage 1) flow-matching sampler. Higher values push the generation more strongly toward the input image condition, improving faithfulness but potentially introducing artifacts. Lower values allow more creative freedom.",
    },
    "ss_guidance_rescale": {
        "label": "SS Guidance Rescale",
        "type": "float",
        "default": 0.7,
        "min": 0.0,
        "max": 1.0,
        "step": 0.05,
        "group": "Sparse Structure",
        "tooltip": "Guidance rescale factor (_CFG rescale_) for Stage 1. Interpolates between the guided and unguided predictions to prevent over-saturation. 0.7 is a good balance; lower values may produce more varied structures, higher values more rigid adherence to the condition.",
    },
    "ss_sampling_steps": {
        "label": "SS Sampling Steps",
        "type": "int",
        "default": 12,
        "min": 1,
        "max": 50,
        "step": 1,
        "group": "Sparse Structure",
        "tooltip": "Number of denoising steps for the Sparse Structure sampler. More steps generally improve quality with diminishing returns. 12 is a good default; reduce to 4–8 for faster (but lower-quality) results, increase to 20+ for marginal quality gains.",
    },
    "ss_rescale_t": {
        "label": "SS Rescale t",
        "type": "float",
        "default": 5.0,
        "min": 0.1,
        "max": 10.0,
        "step": 0.1,
        "group": "Sparse Structure",
        "tooltip": "Time rescaling factor for the Sparse Structure flow-matching ODE. Controls the density of sampling steps across the time horizon. Higher values concentrate more steps near t=0 (the noisy end), which can help capture coarse structure. Tune only if you understand flow-matching sampling.",
    },

    "shape_slat_guidance_strength": {
        "label": "Shape Guidance Strength",
        "type": "float",
        "default": 7.5,
        "min": 1.0,
        "max": 10.0,
        "step": 0.1,
        "group": "Shape",
        "tooltip": "Classifier-free guidance scale for the Shape latent (Stage 2) flow-matching sampler. Controls how strongly the shape generation adheres to the input image and sparse structure. Higher = more faithful but potentially over-sharpened geometry.",
    },
    "shape_slat_guidance_rescale": {
        "label": "Shape Guidance Rescale",
        "type": "float",
        "default": 0.5,
        "min": 0.0,
        "max": 1.0,
        "step": 0.05,
        "group": "Shape",
        "tooltip": "Guidance rescale factor for Stage 2. Prevents over-guidance artifacts in the shape latent. 0.5 provides a balanced trade-off between fidelity and diversity.",
    },
    "shape_slat_sampling_steps": {
        "label": "Shape Sampling Steps",
        "type": "int",
        "default": 12,
        "min": 1,
        "max": 50,
        "step": 1,
        "group": "Shape",
        "tooltip": "Number of denoising steps for the Shape latent sampler. More steps = finer geometric detail but slower. 12 is recommended; 4–8 for quick previews, 20+ for best quality.",
    },
    "shape_slat_rescale_t": {
        "label": "Shape Rescale t",
        "type": "float",
        "default": 3.0,
        "min": 0.1,
        "max": 10.0,
        "step": 0.1,
        "group": "Shape",
        "tooltip": "Time rescaling for the Shape flow-matching ODE. Affects step distribution across the denoising trajectory. Default 3.0 works well; adjust only for advanced tuning.",
    },

    "tex_slat_guidance_strength": {
        "label": "Texture Guidance Strength",
        "type": "float",
        "default": 1.0,
        "min": 0.0,
        "max": 10.0,
        "step": 0.1,
        "group": "Texture",
        "tooltip": "Classifier-free guidance scale for the Texture latent (Stage 3) flow-matching sampler. The default of 1.0 means no guidance (pure sampling), which works best for textures. Increase cautiously — high guidance can cause texture splotches or color bleeding.",
    },
    "tex_slat_guidance_rescale": {
        "label": "Texture Guidance Rescale",
        "type": "float",
        "default": 0.0,
        "min": 0.0,
        "max": 1.0,
        "step": 0.05,
        "group": "Texture",
        "tooltip": "Guidance rescale factor for Stage 3. Since texture guidance is typically 1.0 (no guidance), this rescale has minimal effect at defaults. Relevant only if you increase texture guidance strength above 1.0.",
    },
    "tex_slat_sampling_steps": {
        "label": "Texture Sampling Steps",
        "type": "int",
        "default": 12,
        "min": 1,
        "max": 50,
        "step": 1,
        "group": "Texture",
        "tooltip": "Number of denoising steps for the Texture latent sampler. More steps yield smoother, more detailed PBR textures. 12 is the sweet spot; reduce for speed or increase to 20+ for complex materials.",
    },
    "tex_slat_rescale_t": {
        "label": "Texture Rescale t",
        "type": "float",
        "default": 3.0,
        "min": 0.1,
        "max": 10.0,
        "step": 0.1,
        "group": "Texture",
        "tooltip": "Time rescaling for the Texture flow-matching ODE. Controls step density across the sampling trajectory. Default 3.0 is well-tuned; modify only for research purposes.",
    },

    "mesh_scale": {
        "label": "Mesh Scale",
        "type": "float",
        "default": 1.0,
        "min": 0.1,
        "max": 5.0,
        "step": 0.1,
        "group": "Camera",
        "tooltip": "Scale factor applied to the 3D mesh during camera parameter computation. Affects how the generated 3D object is positioned relative to the camera. 1.0 means the mesh fits the normalized [-0.5, 0.5] bounding box. Rarely needs changing.",
    },
    "extend_pixel": {
        "label": "Extend Pixel",
        "type": "int",
        "default": 0,
        "min": 0,
        "max": 100,
        "step": 1,
        "group": "Camera",
        "tooltip": "Number of pixels to extend the image border when computing camera distance. Non-zero values push the camera further back, ensuring the full object is captured even if it extends slightly beyond the image frame. 0 is fine for most well-cropped images.",
    },
    "image_resolution": {
        "label": "Image Resolution (Camera Estimation)",
        "type": "int",
        "default": 512,
        "min": 256,
        "max": 1024,
        "step": 64,
        "group": "Camera",
        "tooltip": "Resolution at which the input image is processed for MoGe-2 camera estimation. 512 is the recommended default. Higher resolutions may slightly improve camera accuracy but increase MoGe-2 inference time.",
    },
    "max_num_tokens": {
        "label": "Max Num Tokens",
        "type": "int",
        "default": 49152,
        "min": 4096,
        "max": 98304,
        "step": 4096,
        "group": "Advanced",
        "tooltip": "Maximum number of sparse tokens allowed in the cascade pipeline. This caps the complexity of the generated 3D structure. Higher values allow more geometric detail but increase VRAM usage and inference time. 49152 is the tested default.",
    },

    "decimation_target": {
        "label": "GLB Decimation Target",
        "type": "int",
        "default": 100000,
        "min": 10000,
        "max": 10000000,
        "step": 10000,
        "group": "GLB Export",
        "tooltip": "Target triangle count for mesh decimation during GLB export. Higher values preserve more geometric detail but produce larger files and slower UV unwrapping. 100,000 is a good balance for most use cases. Reduce to 50,000 for faster processing; increase to 500,000+ for maximum detail.",
    },
    "texture_size": {
        "label": "GLB Texture Size",
        "type": "int",
        "default": 2048,
        "min": 512,
        "max": 8192,
        "step": 512,
        "group": "GLB Export",
        "tooltip": "Resolution of the baked PBR texture atlas in the exported GLB. 2048 provides good quality with reasonable file size; 1024 is faster and sufficient for web viewers; 4096 gives high detail but may slow down UV unwrapping significantly.",
    },
}


def get_default_parameters() -> dict:
    return {k: v["default"] for k, v in PARAMETER_DEFINITIONS.items()}


def validate_parameters(params: dict) -> dict:
    validated = {}
    for key, defn in PARAMETER_DEFINITIONS.items():
        if key in params:
            val = params[key]
            if defn["type"] == "int":
                val = int(val)
            elif defn["type"] == "float":
                val = float(val)
            elif defn["type"] == "bool":
                val = bool(val)
            validated[key] = val
        else:
            validated[key] = defn["default"]
    return validated
