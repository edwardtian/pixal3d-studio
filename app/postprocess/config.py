"""Refine presets and parameter definitions.

Presets bundle sensible defaults for a target use case. The "game_engine"
preset is the primary one for this pipeline; it bakes normal + AO maps,
uses xatlas UV packing, applies light Taubin smoothing, and runs gltfpack
Draco + KTX2 compression.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RefinePreset:
    key: str
    label: str
    description: str
    params: dict  # default parameter values for this preset


REFINE_PRESETS: dict[str, RefinePreset] = {
    "game_engine": RefinePreset(
        key="game_engine",
        label="Game Engine",
        description=(
            "Optimized for Unity/Unreal: tangent-space normal map + AO baked "
            "from the cleaned high-poly, xatlas UV packing, light Taubin "
            "denoise smoothing, curvature-aware decimation, and gltfpack "
            "Draco + KTX2 compression."
        ),
        params={
            "decimation_target": 100000,
            "texture_size": 2048,
            "refine_smoothing": "light",       # off | light | moderate
            "refine_remesh": True,             # isotropic remesh before decimation
            "refine_watertight": True,         # guarantee closed manifold
            "refine_uv_packer": "xatlas",      # xatlas | cumesh
            "refine_bake_normal": True,
            "refine_bake_ao": True,
            "refine_ao_samples": 64,
            "refine_inpaint": "patch",         # patch | telea | none
            "refine_compression": "gltfpack",  # gltfpack | none
            "refine_draco_quality": 8,         # 1..10 (higher = bigger/crisper)
            "refine_ktx2": False,
            "refine_normal_yflip": "directx",
            "refine_hard_edge_angle": 60,
            "refine_uv_padding": 8,
            "refine_remove_interior": True,
            "refine_floater_ratio": 0.0001,
        },
    ),
    "game_engine_high": RefinePreset(
        key="game_engine_high",
        label="Game Engine (High)",
        description=(
            "Same as Game Engine but with 500k triangles and 4096 textures "
            "for hero assets where quality matters more than file size."
        ),
        params={
            "decimation_target": 500000,
            "texture_size": 4096,
            "refine_smoothing": "light",
            "refine_remesh": True,
            "refine_watertight": True,
            "refine_uv_packer": "xatlas",
            "refine_bake_normal": True,
            "refine_bake_ao": True,
            "refine_ao_samples": 128,
            "refine_inpaint": "patch",
            "refine_compression": "gltfpack",
            "refine_draco_quality": 10,
            "refine_ktx2": False,
            "refine_normal_yflip": "directx",
            "refine_hard_edge_angle": 60,
            "refine_uv_padding": 8,
            "refine_remove_interior": True,
            "refine_floater_ratio": 0.0001,
        },
    ),
    "game_engine_mobile": RefinePreset(
        key="game_engine_mobile",
        label="Game Engine (Mobile)",
        description=(
            "Aggressively optimized for mobile/AR: 30k triangles, 1024 "
            "textures, stronger compression."
        ),
        params={
            "decimation_target": 30000,
            "texture_size": 1024,
            "refine_smoothing": "light",
            "refine_remesh": True,
            "refine_watertight": True,
            "refine_uv_packer": "xatlas",
            "refine_bake_normal": True,
            "refine_bake_ao": True,
            "refine_ao_samples": 32,
            "refine_inpaint": "patch",
            "refine_compression": "gltfpack",
            "refine_draco_quality": 6,
            "refine_ktx2": False,
            "refine_normal_yflip": "directx",
            "refine_hard_edge_angle": 60,
            "refine_uv_padding": 4,
            "refine_remove_interior": True,
            "refine_floater_ratio": 0.0001,
        },
    ),
    "retopology": RefinePreset(
        key="retopology",
        label="Quad Retopology",
        description=(
            "True quad retopology using Instant Meshes: converts the "
            "triangle mesh to a quad-dominant mesh with uniform quad "
            "sizes and good edge flow. Textures are baked from the "
            "original high-poly to compensate. Best for clean topology."
        ),
        params={
            "refine_retopology_mode": "quad",
            "decimation_target": 50000,
            "texture_size": 2048,
            "refine_smoothing": "light",
            "refine_remesh": True,
            "refine_watertight": True,
            "refine_uv_packer": "xatlas",
            "refine_bake_normal": True,
            "refine_bake_ao": True,
            "refine_ao_samples": 64,
            "refine_inpaint": "patch",
            "refine_compression": "gltfpack",
            "refine_draco_quality": 8,
            "refine_ktx2": False,
            "refine_normal_yflip": "directx",
            "refine_hard_edge_angle": 60,
            "refine_uv_padding": 8,
            "refine_remove_interior": True,
            "refine_floater_ratio": 0.0001,
        },
    ),
}


# Parameter definitions for the refine UI (mirrors PARAMETER_DEFINITIONS shape).
REFINE_PARAMETER_DEFINITIONS = {
    "refine_preset": {
        "label": "Refine Preset",
        "type": "select",
        "default": "game_engine",
        "options": [{"value": k, "label": p.label} for k, p in REFINE_PRESETS.items()],
        "group": "Refine",
        "tooltip": "Target use case preset. Each preset bundles sensible defaults for all refine parameters; the values below override the preset.",
    },
    "decimation_target": {
        "label": "Target Triangle Count",
        "type": "int",
        "default": 100000,
        "min": 5000,
        "max": 5000000,
        "step": 5000,
        "group": "Refine",
        "tooltip": "Target triangle count for the refined low-poly mesh. The high-poly source is decimated to this count with curvature-aware edge collapses so silhouettes and feature edges are preserved.",
    },
    "texture_size": {
        "label": "Texture Size",
        "type": "int",
        "default": 2048,
        "min": 512,
        "max": 8192,
        "step": 512,
        "group": "Refine",
        "tooltip": "Resolution of the baked PBR texture atlas. 2048 is a good balance for game assets; 4096 for hero assets; 1024 for mobile/AR.",
    },
    "refine_smoothing": {
        "label": "Smoothing",
        "type": "select",
        "default": "light",
        "options": [
            {"value": "off", "label": "Off"},
            {"value": "light", "label": "Light (Taubin)"},
            {"value": "moderate", "label": "Moderate (Taubin)"},
        ],
        "group": "Refine",
        "tooltip": "Denoise smoothing applied to the high-poly source BEFORE it is used as the bake source. Taubin smoothing is feature-preserving (unlike Laplacian). 'Light' removes spikes without erasing genuine detail; 'Moderate' smooths more aggressively. Disable to preserve every detail.",
    },
    "refine_remesh": {
        "label": "Isotropic Remesh",
        "type": "bool",
        "default": True,
        "group": "Refine",
        "tooltip": "Uniform-triangle isotropic remesh before decimation. Improves shading quality, normal-map fidelity, and UV unwrap. Recommended on.",
    },
    "refine_retopology_mode": {
        "label": "Retopology Mode",
        "type": "select",
        "default": "triangle",
        "options": [
            {"value": "triangle", "label": "Triangle Decimation"},
            {"value": "quad", "label": "Quad Retopology (Instant Meshes)"},
        ],
        "group": "Refine",
        "tooltip": "Triangle: isotropic remesh + curvature-aware decimation (fast, triangle output). Quad: Instant Meshes cross-field quad retopology — produces uniform quad-dominant mesh with good edge flow (slower, but much cleaner topology).",
    },
    "refine_watertight": {
        "label": "Watertight",
        "type": "bool",
        "default": True,
        "group": "Refine",
        "tooltip": "Guarantee a closed manifold (no boundary edges). Required for collision mesh generation in game engines. Disable for open surfaces like cloth or terrain.",
    },
    "refine_uv_packer": {
        "label": "UV Packer",
        "type": "select",
        "default": "xatlas",
        "options": [
            {"value": "xatlas", "label": "xatlas (better packing)"},
            {"value": "cumesh", "label": "CuMesh (faster)"},
        ],
        "group": "Refine",
        "tooltip": "UV atlas packing algorithm. xatlas produces better texel density and fewer wasted atlas pixels; CuMesh is faster but less optimal. Falls back to CuMesh if xatlas is not installed.",
    },
    "refine_bake_normal": {
        "label": "Bake Normal Map",
        "type": "bool",
        "default": True,
        "group": "Refine",
        "tooltip": "Bake a tangent-space normal map from the cleaned high-poly source. This is the single biggest visual-quality win — lets a low-poly mesh render as if it had the high-poly detail.",
    },
    "refine_bake_ao": {
        "label": "Bake AO Map",
        "type": "bool",
        "default": True,
        "group": "Refine",
        "tooltip": "Bake an ambient-occlusion map from the high-poly source. Free realism; cheap to compute.",
    },
    "refine_ao_samples": {
        "label": "AO Samples",
        "type": "int",
        "default": 64,
        "min": 16,
        "max": 512,
        "step": 16,
        "group": "Refine",
        "tooltip": "Number of rays per texel for AO baking. 64 is a good default; 128+ for higher quality; 32 for faster baking.",
    },
    "refine_inpaint": {
        "label": "Texture Inpaint",
        "type": "select",
        "default": "patch",
        "options": [
            {"value": "patch", "label": "Patch-based (better)"},
            {"value": "telea", "label": "TELEA (fast)"},
            {"value": "none", "label": "None"},
        ],
        "group": "Refine",
        "tooltip": "Algorithm to fill texels not covered by any triangle in the UV atlas. Patch-based produces fewer smearing artifacts on large gaps than the legacy TELEA method.",
    },
    "refine_compression": {
        "label": "Compression",
        "type": "select",
        "default": "gltfpack",
        "options": [
            {"value": "gltfpack", "label": "gltfpack (Draco + KTX2)"},
            {"value": "none", "label": "None"},
        ],
        "group": "Refine",
        "tooltip": "Output compression. gltfpack applies Draco geometry compression + KTX2/BasisU texture compression (10-30x smaller GLBs). Requires the gltfpack binary in PATH. Falls back to uncompressed if unavailable.",
    },
    "refine_draco_quality": {
        "label": "Texture Quality",
        "type": "int",
        "default": 8,
        "min": 1,
        "max": 10,
        "step": 1,
        "group": "Refine",
        "tooltip": "Texture encoding quality passed to gltfpack (1..10). Higher = crisper textures but larger file. Applies to KTX2/BasisU and WebP encoding.",
    },
    "refine_ktx2": {
        "label": "KTX2 Textures",
        "type": "bool",
        "default": False,
        "group": "Refine",
        "tooltip": "Convert textures to KTX2/BasisU format (GPU-native, smaller payloads, best for game engines). Disabling uses WebP, which keeps the in-app model-viewer preview working. Requires gltfpack binary.",
    },
    "refine_normal_yflip": {
        "label": "Normal Map Convention",
        "type": "select",
        "default": "directx",
        "options": [
            {"value": "directx", "label": "DirectX / Unreal (-Y green)"},
            {"value": "opengl", "label": "OpenGL / Unity (+Y green)"},
        ],
        "group": "Refine",
        "tooltip": "Tangent-space normal map green-channel convention. Unreal Engine and DirectX expect -Y (inverted green); Unity and OpenGL expect +Y. Baking the correct convention avoids lighting looking wrong in-engine.",
    },
    "refine_hard_edge_angle": {
        "label": "Hard Edge Angle",
        "type": "int",
        "default": 60,
        "min": 0,
        "max": 180,
        "step": 1,
        "group": "Refine",
        "tooltip": "Split normals (crease) along edges whose dihedral angle exceeds this threshold, producing crisp edges for hard-surface assets. 0 disables hard-edge detection (fully smooth shading). 60 degrees is a good default.",
    },
    "refine_uv_padding": {
        "label": "UV Padding (px)",
        "type": "int",
        "default": 8,
        "min": 0,
        "max": 32,
        "step": 1,
        "group": "Refine",
        "tooltip": "Number of texels to bleed each UV chart outward (nearest-neighbor color extension). Prevents seam/edge color bleeding when the engine downsamples textures for mipmaps. Scaled relative to a 2048 atlas. 0 disables.",
    },
    "refine_remove_interior": {
        "label": "Remove Interior Geometry",
        "type": "bool",
        "default": True,
        "group": "Refine",
        "tooltip": "Best-effort removal of hidden interior faces/shells that are occluded by enclosing surfaces. AI meshes often contain internal geometry that wastes triangles and corrupts AO/collision. Falls back to no-op if the visibility test fails.",
    },
    "refine_floater_ratio": {
        "label": "Floater Threshold",
        "type": "float",
        "default": 0.0001,
        "min": 0.0,
        "max": 0.01,
        "step": 0.0001,
        "group": "Refine",
        "tooltip": "Disconnected components smaller than this fraction of the total surface area are treated as floaters and removed. 0 disables floater removal. Lower = keep more fragments.",
    },
}


def get_refine_preset(key: str) -> RefinePreset:
    return REFINE_PRESETS.get(key, REFINE_PRESETS["game_engine"])


def get_default_refine_parameters() -> dict:
    return {k: v["default"] for k, v in REFINE_PARAMETER_DEFINITIONS.items()}


def validate_refine_parameters(params: dict, preset_key: str | None = None) -> dict:
    """Validate refine params, seeded from the chosen preset then overridden."""
    validated = {}
    # Start from preset defaults
    if preset_key and preset_key in REFINE_PRESETS:
        validated.update(REFINE_PRESETS[preset_key].params)
    # Override with explicit user values
    for key, defn in REFINE_PARAMETER_DEFINITIONS.items():
        if key == "refine_preset":
            validated[key] = preset_key or params.get(key, defn["default"])
            continue
        if key in params:
            val = params[key]
            if defn["type"] == "int":
                val = int(val)
            elif defn["type"] == "float":
                val = float(val)
            elif defn["type"] == "bool":
                val = bool(val)
            validated[key] = val
        elif key not in validated:
            validated[key] = defn["default"]
    return validated
