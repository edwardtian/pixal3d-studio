"""Backend registry — mirrors modly's GeneratorRegistry.

https://github.com/lightningpixel/modly/blob/main/api/services/generator_registry.py

Each worker PROCESS keeps its own registry instance. Backends are created
lazily, loaded on demand, and the previously active backend is unloaded when
the worker switches to a different one, so VRAM is shared between backends
instead of being held by all of them at once. The FastAPI process only uses
the class-level metadata (list_backends / parameter schemas) and never loads
a model.
"""

from __future__ import annotations

import threading

from app.backends.base import BackendBase
from app.backends.pixal3d import Pixal3DBackend
from app.backends.triposg import TripoSGBackend
from app.config import settings

# backend id -> class. Adding a new backend = one entry here.
BACKEND_CLASSES: dict[str, type[BackendBase]] = {
    "pixal3d": Pixal3DBackend,
    "triposg": TripoSGBackend,
}


def default_backend_id() -> str:
    d = settings.DEFAULT_BACKEND
    return d if d in BACKEND_CLASSES else "pixal3d"


def backend_supports_refine(backend_id: str) -> bool:
    cls = BACKEND_CLASSES.get(backend_id or "pixal3d")
    return bool(cls is not None and cls.SUPPORTS_REFINE)


def list_backends() -> list[dict]:
    """Metadata for every backend — used by the UI's backend selector."""
    out = []
    for bid, cls in BACKEND_CLASSES.items():
        out.append({
            "id": bid,
            "name": cls.DISPLAY_NAME,
            "description": cls.DESCRIPTION,
            "vram_gb": cls.VRAM_GB,
            "supports_texture": cls.SUPPORTS_TEXTURE,
            "supports_refine": cls.SUPPORTS_REFINE,
            "available": cls.is_available(),
            "default": bid == default_backend_id(),
        })
    return out


class BackendRegistry:
    """Per-process backend lifecycle manager."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instances: dict[str, BackendBase] = {}
        self._active_id: str | None = None

    def get(self, backend_id: str) -> BackendBase:
        """Return the instance for a backend, creating it on first use."""
        with self._lock:
            inst = self._instances.get(backend_id)
            if inst is None:
                cls = BACKEND_CLASSES.get(backend_id)
                if cls is None:
                    raise ValueError(f"Unknown backend: {backend_id}")
                inst = cls()
                self._instances[backend_id] = inst
            return inst

    def activate(self, backend_id: str) -> BackendBase:
        """Switch the active backend, unloading the previous one to free VRAM."""
        inst = self.get(backend_id)
        if self._active_id != backend_id:
            prev = self._instances.get(self._active_id) if self._active_id else None
            if prev is not None and prev is not inst:
                try:
                    prev.unload()
                except Exception as exc:
                    print(f"[Backends] unload({self._active_id}) failed: {exc}")
            self._active_id = backend_id
        if not inst.is_loaded():
            inst.load()
        return inst

    def active(self) -> BackendBase | None:
        if self._active_id is None:
            return None
        return self._instances.get(self._active_id)

    def any_loaded(self) -> bool:
        with self._lock:
            return any(b.is_loaded() for b in self._instances.values())

    def unload_all(self) -> None:
        with self._lock:
            for b in self._instances.values():
                try:
                    b.unload()
                except Exception:
                    pass
            self._active_id = None


# Per-process singleton (worker processes; harmless in the API process).
# NOTE: named "backend_registry" (not "registry") so it never shadows the
# "registry" submodule in import statements like "import app.backends.registry".
backend_registry = BackendRegistry()
