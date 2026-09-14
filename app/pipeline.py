"""Backward-compatibility shim for the Pixal3D pipeline module.

The Pixal3D implementation now lives in ``app.backends.pixal3d`` and is
driven through the backend registry. This module re-exports everything the
rest of the codebase (notably the refine post-process stages) still imports
from here — ``init_pipeline``, ``_safe_simplify``, ``CancelledError``,
``ProgressCallback`` and the module-level ``_pipeline`` singleton.

A module-level ``__getattr__`` (PEP 562) resolves every attribute lazily
from the implementation module, so ``app.pipeline._pipeline`` always
reflects the backend's current state (including after a load/unload cycle).
"""

import sys as _sys
from app.backends import pixal3d as _impl


def __getattr__(name):
    if name == "_impl":
        raise AttributeError(name)
    return getattr(_impl, name)


def __dir__():
    return sorted(set(list(globals().keys()) + dir(_impl)))
