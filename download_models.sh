#!/bin/bash
set -e

MODELS_DIR="$(cd "$(dirname "$0")" && pwd)/models"
mkdir -p "$MODELS_DIR"

export HF_HOME="$MODELS_DIR"

echo "=== Pixal3D Model Downloader ==="
echo "Download directory: $MODELS_DIR"
echo ""

MODELS=(
    "TencentARC/Pixal3D"
    "Ruicheng/moge-2-vitl"
    "camenduru/dinov3-vitl16-pretrain-lvd1689m"
    "briaai/RMBG-2.0"
)

for model in "${MODELS[@]}"; do
    echo "--- Downloading: $model ---"
        hf download "$model" --cache-dir "$MODELS_DIR/hub"
    echo ""
done

echo "=== Download complete ==="
echo "Models cached in: $MODELS_DIR/hub"
echo ""
echo "Total size:"
du -sh "$MODELS_DIR/hub" 2>/dev/null || true
