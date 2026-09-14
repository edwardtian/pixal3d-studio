# Refine Pipeline: Game-Ready Post-Processing for AI-Generated Models

Research note on improving AI-generated meshes (Pixal3D / TRELLIS.2 class image-to-3D
models) for game-development use, and the concrete Tier-1 fixes implemented in the
`refine` post-process pipeline.

## Target

- **Engine**: Unreal Engine (DirectX normal-map convention, MikkTSpace tangents, glTF import).
- The refined output is a single compressed GLB produced by `app/postprocess/run_refine`.

## What the pipeline already does well

`app/postprocess/pipeline.py` runs a credible game-asset chain:

1. **Geometry repair** (`stages/geometry_repair.py`) — decode latent, weld vertices, fill
   holes, repair non-manifold edges, remove degenerate faces, drop small components,
   unify winding, Taubin denoise, build BVH.
2. **Remesh / decimate** (`remesh.py`, `retopology.py`, `quad_retopology.py`) — isotropic
   remesh, curvature-aware quadric decimation, optional Instant Meshes quad retopo.
3. **UV** (`uv_optimize.py`) — xatlas packing with CuMesh fallback.
4. **Texture bake** (`texture_bake.py`) — PBR base/metal/rough/alpha + tangent-space
   normal + AO, sampled from the volumetric latent.
5. **Inpaint** (`texture_inpaint.py`), **PBR finalize** (`pbr_finalize.py`, MikkTSpace
   tangents + alpha mode), **Compression** (`compression.py`, gltfpack), **Validate** (`validate.py`).

## Tier-1 gaps identified and fixed

### 1. Normal map baked flat face normals instead of smooth high-poly normals

The bake used the high-poly's *flat* per-face normals (`_compute_face_normals`), discarding
the smooth per-vertex normals computed in Stage 1. On smooth surfaces this reintroduces
faceting into the baked normal map — the opposite of what a normal map is for.

**Fix** (`texture_bake.py`): interpolate the high-poly's smooth vertex normals
(`ctx.hp_normals`) at each texel's closest point using the barycentric `uvw` returned by
the BVH distance query (`_interpolate_highpoly_normals`).

### 2. No UV padding / bleed

Only uncovered atlas texels were inpainted with a 1px dilation. Game engines generate
mipmaps, so a 1px gutter is insufficient and neighboring chart colors bleed across seams.

**Fix** (`texture_inpaint.py`): add `refine_uv_padding` (default 8px at 2048, scaled by
atlas size). `_bleed_textures` dilates the coverage mask and fills the padding band with
the nearest covered texel color (nearest-neighbor extension via
`scipy.ndimage.distance_transform_edt`) — unlike `cv2.inpaint`, this *extends* the existing
edge color rather than synthesizing it. The subsequent inpaint only handles remaining deep
gaps.

### 3. Normal-map green-channel convention hardcoded

The green channel was always +Y (OpenGL). Unreal/DirectX expect −Y, causing lighting to
look wrong in-engine.

**Fix** (`texture_bake.py`, `config.py`): add `refine_normal_yflip`
(`directx`/`opengl`, default `directx` for Unreal). The green channel is inverted before
encoding when `directx`.

### 4. No hard-edge / flat-shading support

All low-poly meshes got fully smooth vertex normals, giving hard-surface models a
"pillowed" look.

**Fix** (`uv_optimize.py`): add `refine_hard_edge_angle` (default 60°, 0 = off).
`_split_hard_edges` detects dihedral edges above the threshold, splits vertices along them
(union-find over face corners), carries position/UV, and recomputes area-weighted
per-group normals. Downstream tangent computation runs unchanged.

### 5. Fixed-threshold floater removal; no interior-geometry removal

Small disconnected components were removed with a fixed absolute threshold, and nested
interior shells (common in AI meshes) were never removed — wasting triangle budget and
corrupting AO/collision.

**Fix** (`geometry_repair.py`): `refine_floater_ratio` replaces the fixed threshold with a
relative area threshold. `refine_remove_interior` (default on) drops fully-enclosed
watertight shells via `trimesh.contains` containment tests, guarded by a face-count cap
and a no-op fallback for open surfaces.

## Unreal-specific conventions

- **Tangents**: MikkTSpace tangents are computed and exported as the glTF `TANGENT`
  attribute (`pbr_finalize.py`), which Unreal's glTF importer consumes directly.
- **Normal map Y**: DirectX (−Y) is the default (fix #3).
- **Z-up / scale** (future Tier-3): the current export keeps the latent's `[-0.5, 0.5]`
  box and the generation pipeline's axis transform. Consistent real-world scale
  normalization is a future improvement.

## Not yet addressed (Tier 2 / 3)

- Ray-traced AO (current AO is a cavity + proximity heuristic).
- LOD chain and collision proxy generation.
- ORM packed texture (AO/Roughness/Metallic in one texture).
- Scale normalization, extended validation metrics, alpha-to-coverage edge refinement.

## Fixes applied during deployment (gltfpack + xatlas + interior/watertight)

- **gltfpack**: the upstream `gltfpack-linux` release asset no longer exists (404 →
  9-byte "Not Found" binary → "Exec format error"). Switched to `gltfpack-ubuntu.zip`
  (v1.2). gltfpack 1.x dropped Draco, so the flags were rewritten to the valid set:
  `-vp 14 -vt 12 -vn 8 -tq {quality}` plus `-tc` (KTX2/BasisU) or `-tw` (WebP). No
  `-cc`/`-c` (EXT_meshopt) and no `-gt` (would regenerate our MikkTSpace tangents), so
  output stays model-viewer compatible. WebP is the default so the in-app preview works;
  KTX2 is opt-in for pure game-engine export.
- **xatlas**: the installed build returns `(vertex_map, indices, uvs)` from
  `Atlas.get_mesh(0)`, not an object with `.uv`/`.face`/`.vertex_map`. Unpacking fixed.
- **Interior removal**: moved from the high-poly (millions of faces → always skipped) to
  the decimated low-poly in the decimation stage, where it is cheap and also drives the
  watertightness pass on the CPU (Blackwell) path.
- **Normal-map N basis**: the bake now uses the interpolated smooth low-poly vertex
  normals (including hard-edge splits) as the tangent-space normal, instead of flat face
  normals, so the baked map matches what the engine interpolates at render time.
