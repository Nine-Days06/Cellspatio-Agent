"""阶段定义、目录与失败投影的所有权契约。"""

from pathlib import Path

from src.stage_defs import CLUSTER, FILTER, MARKER, PCA, STAGE_DEFINITIONS
from src.ui.sc_stage_defs import STAGE_SPECS


def test_stage_specs_share_domain_definitions() -> None:
    """持久化描述只引用领域定义，不再复制目录或失败阶段。"""
    assert tuple(spec.definition for spec in STAGE_SPECS) == STAGE_DEFINITIONS
    assert len({definition.name for definition in STAGE_DEFINITIONS}) == 4
    assert [definition.directory_name for definition in STAGE_DEFINITIONS] == [
        "sc_filter",
        "sc_pca",
        "sc_clusters",
        "sc_markers",
    ]
    assert (FILTER, PCA, CLUSTER, MARKER) == STAGE_DEFINITIONS


def test_api_does_not_import_ui_stage_specs() -> None:
    """HTTP 层只消费中性阶段定义。"""
    api_dir = Path(__file__).resolve().parents[2] / "src" / "api"
    offenders = [
        path.name
        for path in api_dir.glob("*.py")
        if "src.ui.sc_stage_defs" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_analysis_owns_no_directory_layout_literals() -> None:
    """分析层的目录校验必须走 stage_directory。"""
    analysis_dir = Path(__file__).resolve().parents[2] / "src" / "analysis"
    offenders = [
        path.name
        for path in analysis_dir.glob("sc_*.py")
        if 'root.parent / "sc_' in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
