"""证据卡 schema：字段对齐 RO-Crate/PROV 术语，只允许聚合事实与哈希。

证据卡是本地私有导出物，不入库、不上传；公共准入由票据 17 另定。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "cellspatio-evidence-card/1.0.0"

Gate = Literal[
    "qc_review",
    "pca_dimensions",
    "cluster_review",
    "marker_review",
    "knowledge_qa_review",
]
Outcome = Literal["accepted", "rejected", "needs_changes", "withdrawn"]

_FORBIDDEN_KEYS = {
    "absolute_path",
    "barcode",
    "barcodes",
    "cell_id",
    "cell_ids",
    "coordinates",
    "counts",
    "counts_matrix",
    "directory",
    "donor_id",
    "file_path",
    "input_path",
    "matrix",
    "path",
    "patient_id",
    "raw_counts",
    "root_path",
    "sample_id",
    "subject_id",
    "umap",
    "umap_1",
    "umap_2",
}
_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|^\\\\|/(?:Users|home|mnt|data|tmp|var|opt|srv)/)")
_SAMPLE_RE = re.compile(r"\b(?:GSM|SRR|ERR|SRS|DRS)\d{3,}\b")
_BARCODE_RE = re.compile(r"\b[ACGT]{16}-\d+\b")
_MAX_LIST = 64
_MIN_AGGREGATE_CELLS = 5


class Artifact(BaseModel):
    """文件身份：只保留名称、大小与哈希，不保留路径。"""

    name: str
    sha256: str
    bytes: int = Field(ge=0)


class Subject(BaseModel):
    """证据来源身份：绑定到已校验的 run/attempt 与人工复核哈希。"""

    session_id: str
    run_id: str
    attempt_id: str | None = None
    parent_attempt_id: str | None = None
    binding_sha256: str
    source_revision: int | None = Field(default=None, ge=0)


class Method(BaseModel):
    """方法与执行上下文。"""

    tool: str
    tool_version: str
    protocol: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    environment: dict[str, str] = Field(default_factory=dict)


class Claim(BaseModel):
    """结论与依据；basis 区分事实与推断。"""

    text: str
    basis: Literal["fact", "inference"] = "fact"
    confidence: Literal["high", "medium", "low"] | None = None


class Review(BaseModel):
    """人工复核事实。"""

    status: Outcome
    reviewer: str | None = None
    reason: str | None = None
    reviewed_at: str | None = None


class License(BaseModel):
    """许可与署名；本地卡可空，贡献前必须填。"""

    spdx: str | None = None
    attribution: str | None = None


class Privacy(BaseModel):
    """导出时必须声明的隐私事实。"""

    contains_raw_data: Literal[False] = False
    pii_scan: Literal["passed"] = "passed"
    redactions: list[str] = Field(default_factory=list)


class Provenance(BaseModel):
    """输入、产物与来源标识。"""

    inputs: list[Artifact] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    software: list[str] = Field(default_factory=list)


class EvidenceCard(BaseModel):
    """一张卡对应一次人工 gate；run 只作为上下文。"""

    schema_version: str = SCHEMA_VERSION
    card_id: str
    created_at: str
    gate: Gate
    outcome: Outcome
    subject: Subject
    method: Method
    observation: dict[str, Any]
    claim: Claim
    limitations: list[str] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)
    review: Review
    license: License = Field(default_factory=License)
    privacy: Privacy = Field(default_factory=Privacy)

    def to_json(self, indent: int = 2) -> str:
        """导出为可脱机阅读的 JSON。"""
        return self.model_dump_json(indent=indent)


def json_schema() -> dict[str, Any]:
    """导出 JSON Schema，供外部工具校验。"""
    return EvidenceCard.model_json_schema()


def privacy_violations(card: EvidenceCard) -> list[str]:
    """扫描禁止字段、绝对路径、样本级 ID、barcode 与疑似逐细胞长列表。"""
    violations: list[str] = []

    def walk(value: Any, prefix: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "packages" and isinstance(item, dict):
                    # 包名可能恰好是 Matrix 等禁止词；版本值仍需扫描。
                    for name, version in item.items():
                        walk(version, f"{prefix}.{key}.{name}")
                    continue
                # 聚类协议里的 umap 是方法参数名，不是嵌入坐标。
                is_protocol_umap = key.lower() == "umap" and prefix == "card.method.protocol"
                if key.lower() in _FORBIDDEN_KEYS and not is_protocol_umap:
                    violations.append(f"{prefix}.{key} 是禁止字段")
                walk(item, f"{prefix}.{key}")
        elif isinstance(value, list):
            if len(value) > _MAX_LIST:
                violations.append(f"{prefix} 列表过长（{len(value)}），疑似逐细胞数据")
            for index, item in enumerate(value):
                walk(item, f"{prefix}[{index}]")
        elif isinstance(value, str):
            if _PATH_RE.search(value):
                violations.append(f"{prefix} 含绝对路径")
            if _SAMPLE_RE.search(value):
                violations.append(f"{prefix} 含样本级 ID")
            if _BARCODE_RE.search(value):
                violations.append(f"{prefix} 含 barcode")

    payload = card.model_dump(mode="json")
    walk(payload, "card")
    cells = card.observation.get("cells")
    if isinstance(cells, int) and cells < _MIN_AGGREGATE_CELLS:
        violations.append(f"card.observation.cells 小于聚合阈值 {_MIN_AGGREGATE_CELLS}")
    for index, item in enumerate(card.observation.get("cluster_sizes") or []):
        if isinstance(item, dict) and isinstance(item.get("cells"), int):
            if item["cells"] < _MIN_AGGREGATE_CELLS:
                violations.append(
                    f"card.observation.cluster_sizes[{index}] 小于聚合阈值 {_MIN_AGGREGATE_CELLS}"
                )
    return violations


def validate_card(card: EvidenceCard) -> EvidenceCard:
    """schema 校验之后再做隐私校验；失败直接拒绝导出。"""
    violations = privacy_violations(card)
    if violations:
        raise ValueError("证据卡隐私校验失败：" + "; ".join(violations))
    return card
