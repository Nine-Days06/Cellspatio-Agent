"""四个受控阶段的领域定义；目录与失败投影的唯一来源。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StageDefinition:
    """阶段在产物目录与失败响应中的稳定身份。"""

    name: str
    directory_name: str
    failure_stage: str


FILTER = StageDefinition("filter", "sc_filter", "filter_review")
PCA = StageDefinition("pca", "sc_pca", "pca_review")
CLUSTER = StageDefinition("cluster", "sc_clusters", "cluster_review")
MARKER = StageDefinition("marker", "sc_markers", "marker_review")
STAGE_DEFINITIONS = (FILTER, PCA, CLUSTER, MARKER)


def stage_directory(definition: StageDefinition, root: Path, attempt_id: str) -> tuple[Path, Path]:
    """返回阶段产物父目录与本次 attempt 目录。"""
    parent = root.parent / definition.directory_name
    return parent, parent / attempt_id


def stage_failure(definition: StageDefinition, error: str) -> dict[str, Any]:
    """统一失败投影：技术失败，科学状态仍未复核。"""
    return {
        "status": "failed",
        "stage": definition.failure_stage,
        "scientific_status": "unreviewed",
        "error": error,
    }
