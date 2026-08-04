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


def overall_progress(subtask_index: int, subtask_step: int, subtask_total_steps: int) -> int:
    """Compute overall 0-100 progress.

    `subtask_index` is 1-based. `subtask_step`/`subtask_total_steps` describe
    progress within the current sub-task (any non-positive total is treated as
    "just started" = 0).
    """
    if subtask_index <= 0:
        return 0
    completed_weight = 0.0
    for s in SUBTASKS:
        if s.index < subtask_index:
            completed_weight += s.weight
    current = SUBTASK_BY_INDEX.get(subtask_index)
    if current is None:
        return int(round(completed_weight * 100))
    frac = 0.0
    if subtask_total_steps and subtask_total_steps > 0:
        frac = max(0.0, min(1.0, subtask_step / subtask_total_steps))
    return int(round((completed_weight + current.weight * frac) * 100))
