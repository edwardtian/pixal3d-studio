#!/bin/bash
set -e

IMAGE_NAME="pixal3d-studio"
CONTAINER_NAME="pixal3d-studio"

# CUDA arch flags
# CUDA_ARCH: for PyTorch C++ extensions (nvdiffrast, CuMesh, etc.)
# NATTEN_CUDA_ARCH: float string for natten, e.g. "12.0" => sm_120, "9.0" => sm_90, "8.6" => sm_86
CUDA_ARCH="${CUDA_ARCH:-12.0}"
NATTEN_CUDA_ARCH="${NATTEN_CUDA_ARCH:-12.0}"

echo "=== Building Podman image (CUDA_ARCH=${CUDA_ARCH}, NATTEN_CUDA_ARCH=${NATTEN_CUDA_ARCH}) ==="
echo "Note: Building GPU packages from source — this may take 30-60 minutes."
podman build \
    --build-arg TORCH_CUDA_ARCH_LIST="${CUDA_ARCH}" \
    --build-arg NATTEN_CUDA_ARCH="${NATTEN_CUDA_ARCH}" \
    -t "$IMAGE_NAME" .

echo "=== Stopping old container (if any) ==="
podman rm -f "$CONTAINER_NAME" 2>/dev/null || true

# Create .env from template if it doesn't exist
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example (edit it to customize settings)"
fi

# Check if models are downloaded
if [ ! -d "models/hub" ] || [ -z "$(ls -A models/hub 2>/dev/null)" ]; then
    echo ""
    echo "WARNING: models/hub/ is empty. Run ./download_models.sh first!"
    echo "  HF_TOKEN=hf_your_token ./download_models.sh"
    echo ""
fi

echo "=== Starting container ==="
podman run -d \
    --name "$CONTAINER_NAME" \
    --env-file .env \
    -p 8000:8000 \
    --device nvidia.com/gpu=all \
    -v "$(pwd)/data:/app/data" \
    -v "$(pwd)/models:/app/models" \
    "$IMAGE_NAME"

echo ""
echo "=== Pixal3D Studio is starting ==="
echo "Access at: http://localhost:8000"
echo "Default admin: admin / admin123"
echo ""
echo "View logs: podman logs -f $CONTAINER_NAME"
