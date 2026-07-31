FROM docker.io/nvidia/cuda:12.8.0-cudnn-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y \
    python3.10 \
    python3.10-dev \
    python3.10-venv \
    python3-pip \
    git \
    cmake \
    build-essential \
    ninja-build \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libopenexr-dev \
    libjpeg-dev \
    && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/bin/python3.10 /usr/bin/python && \
    ln -sf /usr/bin/python3.10 /usr/bin/python3 && \
    python -m pip install --upgrade pip setuptools wheel

WORKDIR /app

# Clone TRELLIS.2 (with eigen submodule needed by o-voxel)
RUN git clone https://github.com/microsoft/TRELLIS.2.git /opt/trellis2 && \
    cd /opt/trellis2 && git submodule update --init o-voxel/third_party/eigen

# Clone Pixal3D (provides pixal3d package + assets)
RUN git clone https://github.com/TencentARC/Pixal3D.git /opt/pixal3d

# Install PyTorch with CUDA 12.8 (required for Blackwell sm_120+)
RUN pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128

# Install basic Python dependencies (versions pinned to match Pixal3D requirements)
RUN pip install \
    "imageio==2.37.2" "imageio-ffmpeg==0.6.0" "tqdm==4.67.1" "easydict==1.13" \
    "opencv-python-headless==4.12.0.88" "ninja" "trimesh==4.10.1" \
    "transformers==4.57.3" "zstandard==0.25.0" "kornia==0.8.2" "timm==1.0.22" \
    "diffusers==0.37.1" "accelerate==1.13.0" "plyfile==1.1.3" "pillow==12.0.0" \
    einops safetensors sentencepiece scipy scikit-learn

# ---- Build GPU packages from source (compatible with Blackwell) ----
# Set CUDA arch for PyTorch C++ extensions (no GPU at build time, must specify)
ARG TORCH_CUDA_ARCH_LIST="12.0"
ENV TORCH_CUDA_ARCH_LIST=${TORCH_CUDA_ARCH_LIST}

# nvdiffrast (GPU mesh rasterizer)
RUN git clone -b v0.4.0 https://github.com/NVlabs/nvdiffrast.git /tmp/nvdiffrast && \
    pip install /tmp/nvdiffrast --no-build-isolation && rm -rf /tmp/nvdiffrast

# nvdiffrec (PBR split-sum renderer)
RUN git clone -b renderutils https://github.com/JeffreyXiang/nvdiffrec.git /tmp/nvdiffrec && \
    pip install /tmp/nvdiffrec --no-build-isolation && rm -rf /tmp/nvdiffrec

# CuMesh (CUDA mesh utilities — sm_90+PTX for Blackwell JIT compat)
RUN git clone --recursive https://github.com/JeffreyXiang/CuMesh.git /tmp/CuMesh && \
    TORCH_CUDA_ARCH_LIST="9.0+PTX" pip install /tmp/CuMesh --no-build-isolation && rm -rf /tmp/CuMesh

# FlexGEMM (sparse convolution via Triton)
RUN git clone --recursive https://github.com/JeffreyXiang/FlexGEMM.git /tmp/FlexGEMM && \
    pip install /tmp/FlexGEMM --no-build-isolation && rm -rf /tmp/FlexGEMM

# O-Voxel (from TRELLIS.2 repo — sm_90+PTX for Blackwell JIT compat)
RUN TORCH_CUDA_ARCH_LIST="9.0+PTX" pip install /opt/trellis2/o-voxel --no-build-isolation

# natten (neighborhood attention — NATTEN_CUDA_ARCH is a float string, e.g. "12.0" => 120 => sm_120)
ARG NATTEN_CUDA_ARCH=12.0
RUN NATTEN_CUDA_ARCH=${NATTEN_CUDA_ARCH} NATTEN_N_WORKERS=$(nproc) \
    pip install natten==0.21.0 --no-build-isolation || \
    (echo "[WARNING] natten 0.21.0 build failed, trying latest from GitHub…" && \
     NATTEN_CUDA_ARCH=${NATTEN_CUDA_ARCH} NATTEN_N_WORKERS=$(nproc) \
     pip install git+https://github.com/SHI-Labs/NATTEN.git --no-build-isolation)

# MoGe (camera estimation — install without deps to avoid utils3d conflict)
RUN pip install --no-deps git+https://github.com/microsoft/MoGe.git

# utils3d 0.0.2 (force-install the Pixal3D-compatible version)
RUN pip install --force-reinstall --no-deps \
    https://github.com/LDYang694/Storages/releases/download/20260430/utils3d-0.0.2-py3-none-any.whl

# Install our web app dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy Pixal3D assets (HDRI maps, sample images)
RUN cp -r /opt/pixal3d/assets /app/assets || true

# Copy our application code
COPY . .

RUN mkdir -p data/uploads data/outputs data/renders data/db models

# Make trellis2 and pixal3d packages importable
ENV PYTHONPATH="/opt/trellis2:/opt/pixal3d:${PYTHONPATH}"

ENV OPENCV_IO_ENABLE_OPENEXR=1
ENV PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
# Use SDPA instead of flash_attn (broader GPU compatibility including Blackwell)
ENV ATTN_BACKEND=sdpa
# Limit CPU threads for xatlas/mesh processing to prevent deadlocks
ENV OMP_NUM_THREADS=4
# HuggingFace model cache — mapped from host's models/ directory at runtime
ENV HF_HOME=/app/models

EXPOSE 8000

CMD ["python", "run.py"]
