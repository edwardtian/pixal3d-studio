# Pixal3D Studio

A multi-user web application for [Pixal3D](https://github.com/TencentARC/Pixal3D) — high-fidelity pixel-aligned image-to-3D generation.

## Features

1. **User Authentication & Management**
   - JWT-based login/register
   - Two roles: `admin` and `user`
   - Admin panel for user management (change roles, activate/deactivate, delete users)
   - Default admin account created on first startup (`admin` / `admin123`)

2. **All Configurable Parameters Exposed**
   - Every inference parameter is exposed in the UI with an informational tooltip
   - Parameters are organized into groups: Base, Sparse Structure, Shape, Texture, Camera, Advanced, GLB Export
   - 23 parameters total including guidance strength, sampling steps, rescale factors, FOV, resolution, decimation, texture size, etc.

3. **Selectable Generation Backends**
   - The create view lets the user pick the generation backend (Pixal3D or TripoSG)
   - The parameter form re-renders from the selected backend's schema — each backend exposes only its own parameters
   - Backend choice is stored with each task and preset, so re-submits and presets restore the right schema
   - See [Generation Backends](#generation-backends) below

4. **Task Queue & History**
   - Multiple users can submit tasks concurrently into a shared queue
   - Tasks are processed one-by-one (FIFO) by a background worker
   - Each user sees only their own task history
   - Real-time progress tracking (stage name, step/total, progress bar)
   - Queue position displayed while waiting
   - 3D model preview (model-viewer) and GLB download for completed tasks
   - Preview renders in multiple modes (normal, clay, base color, HDRI shaded)

## Architecture

```
┌──────────────┐     ┌──────────────────┐     ┌─────────────┐
│   Frontend   │────▶│  FastAPI Backend  │────▶│   Worker    │
│  (HTML/JS)   │◀────│  (Auth, APIs)     │◀────│  (GPU task) │
└──────────────┘     └────┬─────────────┘     └─────────────┘
                          │
                     ┌────▼─────┐
                     │  SQLite  │
                     │ Database │
                     └──────────┘
```

- **Backend**: FastAPI + SQLAlchemy (async SQLite)
- **Frontend**: Vanilla HTML/JS/CSS with [model-viewer](https://modelviewer.dev/) for 3D preview
- **Queue**: In-process async queue with single-worker GPU processing
- **Container**: Podman (NVIDIA CUDA base image)

## Project Structure

```
pixel3d/
├── app/
│   ├── main.py          # FastAPI app entry
│   ├── config.py        # Settings (env-based)
│   ├── database.py      # Async DB setup + auth helpers
│   ├── models.py        # SQLAlchemy models (User, Task)
│   ├── schemas.py       # Pydantic schemas
│   ├── auth.py          # JWT auth + role guards
│   ├── parameters.py    # Per-backend parameter definitions + validation
│   ├── stages.py        # Per-backend sub-task / progress layout
│   ├── pipeline.py      # Back-compat shim (→ app.backends.pixal3d)
│   ├── backends/        # Generation backend registry (modly-style)
│   │   ├── base.py      #   BackendBase contract
│   │   ├── registry.py  #   per-process backend registry
│   │   ├── pixal3d.py   #   Pixal3D/TRELLIS.2 backend
│   │   └── triposg.py   #   VAST TripoSG backend
│   ├── worker.py        # Background task processor
│   ├── queue.py         # Async task queue
│   └── routers/
│       ├── auth.py      # /api/auth/*
│       ├── users.py     # /api/users/* (admin)
│       ├── tasks.py     # /api/tasks/*
│       ├── backends.py  # /api/backends (list + per-backend params)
│       ├── parameters.py# /api/parameters
│       └── queue.py     # /api/queue/*
├── static/
│   ├── index.html       # SPA shell
│   ├── css/style.css
│   └── js/app.js        # Full client-side app
├── data/                # Runtime data (uploads, outputs, renders, DB)
├── requirements.txt     # Web app dependencies
├── requirements-gpu.txt # GPU/ML dependencies
├── Containerfile        # Podman build file
├── run_podman.sh        # Build & run script
├── run.py               # Python entry point
└── .env.example         # Environment config template
```

## Generation Backends

The worker processes generate meshes through a **backend registry** (app/backends/), modeled on
[modly](https://github.com/lightningpixel/modly)'s generator architecture: each backend is a
self-contained image -> GLB pipeline with its own parameter schema, sub-task/progress layout and
VRAM requirements. Workers load a backend lazily on the first task that requests it and unload the
previous one when switching, so VRAM is shared between backends.

The create view exposes a **Backend** selector; changing it re-fetches the backend's parameter
schema (GET /api/backends/{id}/parameters) and re-renders the form accordingly. The selected
backend is saved inside each task's parameters (and presets), so re-submitting a task or applying
a preset restores the matching schema automatically.

| Backend  | Model | Output | Refine | VRAM | Notes |
|----------|-------|--------|--------|------|-------|
| pixal3d | Tencent Pixal3D / TRELLIS.2 | Textured GLB (PBR base color, metallic, roughness) | yes | ~18 GB (or ~10-12 GB low-VRAM) | The original pipeline: sparse structure -> shape latent -> texture latent, camera estimation via MoGe-2 |
| triposg | VAST-AI/TripoSG | Geometry + base-color texture projected from the input photo (optional; neutral PBR material) | no | ~8-10 GB | Rectified-flow transformer; DiffDMC (flash) or Marching Cubes decoder; RMBG-2.0 background removal; MoGe-2 camera estimate + xatlas/nvdiffrast texture bake |

### API

- `GET /api/backends` — list backends with capabilities (id, name, description, VRAM, supports_texture, supports_refine, available, default)
- `GET /api/backends/{backend_id}/parameters` — parameter schema for one backend
- `GET /api/parameters?backend={backend_id}` — schema for a backend (defaults to DEFAULT_BACKEND)
- `POST /api/tasks` — the parameters JSON now carries `"backend": "<id>"` (validated server-side)

### Configuration

```ini
# .env
DEFAULT_BACKEND=pixal3d            # backend selected by default in the UI
TRIPOSG_MODEL_PATH=VAST-AI/TripoSG # HF repo id or local path
RMBG_MODEL_NAME=briaai/RMBG-2.0   # bg-removal model for the TripoSG preprocess
```

The TripoSG backend needs the `triposg` package (cloned into /opt/triposg in the container) and
the `diso` CUDA extension — both are installed by the Containerfile. VAST-AI/TripoSG is added
to download_models.sh (the repo is not gated).

Note on diffusers versions: `VAST-AI/TripoSG`'s `model_index.json` references custom triposg
classes. diffusers >= 0.32 no longer resolves those from arbitrary module paths, so the backend
tries the plain `TripoSGPipeline.from_pretrained(...)` first and falls back to constructing the
pipeline manually from its components (`vae`, `transformer`, `scheduler`, `image_encoder_dinov2`,
`feature_extractor_dinov2`) — which works on every diffusers version.

### Adding another backend

1. Create app/backends/<id>.py with a BackendBase subclass (see app/backends/base.py).
2. Register it in BACKEND_CLASSES in app/backends/registry.py.
3. Add its parameter schema in app/parameters.py and its sub-tasks in app/stages.py.
4. No other changes are needed — the UI, workers and validation pick it up automatically.

## Quick Start (Podman)

### Prerequisites

- [Podman](https://podman.io/) installed
- NVIDIA GPU with CUDA 12.8+ and [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)
- ~20GB disk space (for image + model weights)
- **Blackwell GPUs (sm_120)**: supported via CUDA 12.8 base + source builds.
  natten compiles for sm_90 and uses PTX JIT forward-compatibility.
- **Older GPUs (sm_80–sm_90)**: set `CUDA_ARCH` and `NATTEN_CUDA_ARCH` to your arch, e.g. `CUDA_ARCH=9.0 NATTEN_CUDA_ARCH=90 ./run_podman.sh`

### Build & Run

**Step 1: Download models** (on the host, before starting the container)

```bash
# The briaai/RMBG-2.0 model is gated — you must:
#   1. Accept the license at https://huggingface.co/briaai/RMBG-2.0
#   2. Get a token at https://huggingface.co/settings/tokens
#   3. Pass it to the download script:

HF_TOKEN=hf_your_token_here ./download_models.sh

# Or store the token for reuse:
echo hf_your_token_here > models/.hf_token
./download_models.sh
```

This downloads all required models (~10 GB) into `models/hub/`.

**Step 2: Build and run the container**

```bash
cp .env.example .env
# Edit .env: change SECRET_KEY and ADMIN_PASSWORD

# For Blackwell (RTX PRO 6000, etc.):
./run_podman.sh

# For other GPUs, set the CUDA arch:
CUDA_ARCH=9.0 NATTEN_CUDA_ARCH=9.0 ./run_podman.sh   # e.g. for A100/H100
```

**Note:** The first build compiles GPU packages (natten, CuMesh, FlexGEMM, etc.) from source and may take 30–60 minutes.

Access at **http://localhost:8000**

### Manual Podman Commands

```bash
# Build
podman build -t pixal3d-studio .

# Run
podman run -d \
    --name pixal3d-studio \
    --env-file .env \
    -p 8000:8000 \
    --device nvidia.com/gpu=all \
    -v $(pwd)/data:/app/data \
    pixal3d-studio

# View logs
podman logs -f pixal3d-studio

# Stop
podman stop pixal3d-studio && podman rm pixal3d-studio
```

## Local Development (without GPU)

The web framework can run without the GPU dependencies for testing the UI/API:

```bash
pip install -r requirements.txt
python run.py
```

Tasks will fail with a `ModuleNotFoundError` since the ML libraries aren't installed, but all other functionality (auth, queue, history, admin) works.

## Parameters Reference

| Parameter | Default | Group | Description |
|-----------|---------|-------|-------------|
| `seed` | 42 | Base | Random seed for reproducibility |
| `resolution` | 1536 | Base | Pipeline resolution (1024 or 1536) |
| `manual_fov` | -1 (Auto) | Base | Camera FOV in degrees (-1 = auto via MoGe-2) |
| `low_vram` | false | Base | Load models on-demand to reduce VRAM |
| `ss_guidance_strength` | 7.5 | Sparse Structure | CFG scale for Stage 1 |
| `ss_guidance_rescale` | 0.7 | Sparse Structure | CFG rescale for Stage 1 |
| `ss_sampling_steps` | 12 | Sparse Structure | Denoising steps for Stage 1 |
| `ss_rescale_t` | 5.0 | Sparse Structure | Time rescaling for Stage 1 |
| `shape_slat_guidance_strength` | 7.5 | Shape | CFG scale for Stage 2 |
| `shape_slat_guidance_rescale` | 0.5 | Shape | CFG rescale for Stage 2 |
| `shape_slat_sampling_steps` | 12 | Shape | Denoising steps for Stage 2 |
| `shape_slat_rescale_t` | 3.0 | Shape | Time rescaling for Stage 2 |
| `tex_slat_guidance_strength` | 1.0 | Texture | CFG scale for Stage 3 |
| `tex_slat_guidance_rescale` | 0.0 | Texture | CFG rescale for Stage 3 |
| `tex_slat_sampling_steps` | 12 | Texture | Denoising steps for Stage 3 |
| `tex_slat_rescale_t` | 3.0 | Texture | Time rescaling for Stage 3 |
| `mesh_scale` | 1.0 | Camera | Mesh scale for camera computation |
| `extend_pixel` | 0 | Camera | Border extension for camera distance |
| `image_resolution` | 512 | Camera | Resolution for MoGe-2 camera estimation |
| `max_num_tokens` | 49152 | Advanced | Max sparse tokens in cascade |
| `decimation_target` | 1000000 | GLB Export | Target triangle count |
| `texture_size` | 4096 | GLB Export | Texture atlas resolution |

## API Endpoints

### Auth
- `POST /api/auth/register` — Register new user
- `POST /api/auth/login` — Login, returns JWT
- `GET /api/auth/me` — Get current user info

### Users (admin only)
- `GET /api/users` — List all users
- `PATCH /api/users/{id}/role?role=admin|user` — Change role
- `PATCH /api/users/{id}/active?is_active=true|false` — Toggle active
- `DELETE /api/users/{id}` — Delete user

### Tasks
- `POST /api/tasks/upload` — Upload image (multipart)
- `POST /api/tasks` — Create task (image_filename + parameters)
- `GET /api/tasks` — List current user's tasks
- `GET /api/tasks/{id}` — Get task detail/status
- `GET /api/tasks/{id}/image` — Get input image
- `GET /api/tasks/{id}/render/{mode}/{frame}` — Get preview render
- `GET /api/tasks/{id}/download` — Download GLB
- `DELETE /api/tasks/{id}` — Delete task

### Parameters & Queue
- `GET /api/parameters` — Get all parameter definitions with tooltips
- `GET /api/queue/status` — Get queue status and your position

## License

This project wraps [Pixal3D](https://github.com/TencentARC/Pixal3D) which is MIT-licensed.
