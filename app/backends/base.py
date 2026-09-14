"""Backend contract for image-to-3D generation backends.

Modeled on modly's ``BaseGenerator`` contract (see
https://github.com/lightningpixel/modly, ``api/services/generators/base.py``):
each backend encapsulates a complete image -> GLB pipeline — model loading,
generation, mesh extraction and preview rendering — plus the parameter schema
and sub-task layout that the UI and the worker processes use to drive it.

The registry in ``app.backends.registry`` keeps one instance per backend in
each worker process and unloads the previously active backend when switching
(so VRAM is shared between backends instead of being held by all of them).
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import Optional


class CancelledError(Exception):
    """Raised when a task is cancelled between sub-tasks."""


class ProgressCallback:
    """Shared progress reporter used by workers and post-process stages."""

    def __init__(self):
        self.subtask_index = 0
        self.subtask_name = ""
        self.subtask_step = 0
        self.subtask_total_steps = 0
        # Legacy single progress fields (mirror the DB columns progress_step /
        # progress_total, kept for compatibility).
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


class BackendBase(ABC):
    # ------------------------------------------------------------------ #
    # Metadata — override in each subclass
    # ------------------------------------------------------------------ #
    BACKEND_ID: str = ""
    DISPLAY_NAME: str = ""
    DESCRIPTION: str = ""
    VRAM_GB: int = 0                # recommended minimum VRAM
    SUPPORTS_TEXTURE: bool = True   # does the output carry PBR textures?
    SUPPORTS_REFINE: bool = True    # can results run through the refine pipeline?

    def __init__(self) -> None:
        self._loaded = False

    # ------------------------------------------------------------------ #
    # Capability / lifecycle
    # ------------------------------------------------------------------ #

    @classmethod
    def is_available(cls) -> bool:
        """Whether the model weights are present locally.

        ``False`` only means "not downloaded yet" — workers still load the
        backend on demand and HuggingFace downloads the weights on first use
        (mirrors modly's auto-download behavior).
        """
        return True

    def is_loaded(self) -> bool:
        return self._loaded

    @abstractmethod
    def load(self) -> None:
        """Load the model(s) onto the GPU. Sets ``self._loaded = True``."""
        ...

    def unload(self) -> None:
        """Release memory so another backend can use the GPU."""
        self._loaded = False
        import gc
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Schema (for the UI and validation)
    # ------------------------------------------------------------------ #

    @abstractmethod
    def params_schema(self) -> dict:
        """Return ``{param_key: definition}`` in the app's parameter format."""
        ...

    def subtasks(self) -> list:
        """Ordered sub-task list with progress weights (see app.stages)."""
        from app.stages import subtasks_for
        return subtasks_for(self.BACKEND_ID)

    # ------------------------------------------------------------------ #
    # Inference
    # ------------------------------------------------------------------ #

    @abstractmethod
    def run(
        self,
        image_path: str,
        params: dict,
        progress: ProgressCallback,
        renders_dir: str,
        output_path: str,
        cancel_event: Optional[threading.Event] = None,
    ) -> dict:
        """Run the full image -> GLB pipeline.

        ``output_path`` receives the final GLB. Returns a result dict::

            {
                "render_paths": {mode: [file, ...], ...},  # may be empty
                "state_path": str,          # saved latent for refine ("" if none)
                "preprocessed_image_path": str,
                "camera_angle_x": str,
                "distance": str,
            }
        """
        ...

    @staticmethod
    def _check_cancel(cancel_event: Optional[threading.Event]) -> None:
        """Raise CancelledError if the cancel event is set."""
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError("Task cancelled")
