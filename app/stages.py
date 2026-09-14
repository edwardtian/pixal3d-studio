"""Sub-task (stage) definitions for a 3D generation task.

Each task progresses through a fixed ordered list of sub-tasks. Weights sum to
1.0 and are used to compute the overall (0-100) progress across the whole task
from the within-subtask step/total progress.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SubTask:
    index: int          # 1-based
    name: str           # short display name
    weight: float       # contribution to overall progress (0..1)


SUBTASKS: list[SubTask] = [
    SubTask(1, "Preprocessing & Camera Estimation", 0.05),
    SubTask(2, "Stage 1: Sparse Structure",         0.20),
    SubTask(3, "Stage 2: Shape SLAT",               0.25),
    SubTask(4, "Stage 3: Texture SLAT",             0.25),
    SubTask(5, "Rendering preview views",           0.10),
    SubTask(6, "GLB Extraction",                    0.15),
]

SUBTASK_TOTAL = len(SUBTASKS)
SUBTASK_BY_INDEX = {s.index: s for s in SUBTASKS}


# Refine pipeline sub-tasks. Used when a task is a refine pass (has a
# parent_task_id). Weights sum to 1.0.
SUBTASKS_REFINE: list[SubTask] = [
    SubTask(1, "Repair High-Poly Source", 0.10),
    SubTask(2, "Isotropic Remesh",        0.08),
    SubTask(3, "Curvature Decimation",    0.10),
    SubTask(4, "UV Optimize",             0.12),
    SubTask(5, "Texture Bake (PBR+N+AO)", 0.25),
    SubTask(6, "Texture Inpaint",         0.08),
    SubTask(7, "PBR Finalize",            0.07),
    SubTask(8, "Compress & Export",       0.15),
    SubTask(9, "Validate",                0.05),
]

SUBTASK_REFINE_TOTAL = len(SUBTASKS_REFINE)
SUBTASK_REFINE_BY_INDEX = {s.index: s for s in SUBTASKS_REFINE}


# TripoSG backend sub-tasks. Weights sum to 1.0.
SUBTASKS_TRIPOSG: list[SubTask] = [
    SubTask(1, "Preprocess (Background Removal)", 0.07),
    SubTask(2, "Flow-Matching Sampling",         0.58),
    SubTask(3, "Mesh Extraction",                0.09),
    SubTask(4, "Mesh Simplification",            0.05),
    SubTask(5, "Texture Projection & Bake",      0.10),
    SubTask(6, "Preview Renders",                0.06),
    SubTask(7, "GLB Export",                     0.05),
]

# backend id -> generation sub-task list
BACKEND_SUBTASKS: dict[str, list[SubTask]] = {
    "pixal3d": SUBTASKS,
    "triposg": SUBTASKS_TRIPOSG,
}


def subtasks_for(backend_id: str | None = None, refine: bool = False) -> list[SubTask]:
    """Return the sub-task list for a backend (refine passes always use the
    refine list, which is backend-independent)."""
    if refine:
        return SUBTASKS_REFINE
    return BACKEND_SUBTASKS.get(backend_id or "pixal3d", SUBTASKS)


def overall_progress(subtask_index: int, subtask_step: int, subtask_total_steps: int,
                     backend_id: str | None = None, refine: bool = False) -> int:
    """Compute overall 0-100 progress.

    'subtask_index' is 1-based. 'subtask_step'/'subtask_total_steps' describe
    progress within the current sub-task (any non-positive total is treated as
    "just started" = 0). 'backend_id' selects the sub-task weight layout; when
    'refine' is True, uses the refine sub-task list (for tasks with a
    parent_task_id).
    """
    subtasks = subtasks_for(backend_id, refine=refine)
    by_index = {s.index: s for s in subtasks}
    if subtask_index <= 0:
        return 0
    completed_weight = 0.0
    for s in subtasks:
        if s.index < subtask_index:
            completed_weight += s.weight
    current = by_index.get(subtask_index)
    if current is None:
        return int(round(completed_weight * 100))
    frac = 0.0
    if subtask_total_steps and subtask_total_steps > 0:
        frac = max(0.0, min(1.0, subtask_step / subtask_total_steps))
    return int(round((completed_weight + current.weight * frac) * 100))
