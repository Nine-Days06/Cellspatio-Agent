"""从本地 run/attempt 与人工复核事实生成证据卡。"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.analysis.review_facts import cluster_review_facts, marker_review_facts
from src.analysis.sc_pca import DIMENSION_RECORD_VERSION, current_dimensions, dimension_binding
from src.evidence.schema import (
    Artifact,
    Claim,
    EvidenceCard,
    License,
    Method,
    Provenance,
    Review,
    Subject,
    validate_card,
)
from src.ui.run_snapshot import RunSnapshot
from src.ui.session_store import SessionStore


def _now() -> str:
    """当前 UTC 时间（ISO 8601）。"""
    return datetime.now(timezone.utc).isoformat()


def _new_card_id() -> str:
    """生成稳定的卡标识。"""
    return f"evc-{uuid.uuid4().hex}"


def _sha256(path: Path) -> str:
    """流式计算文件 SHA256。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_artifact(path: Path) -> Artifact:
    """只保留名称、大小与哈希，丢弃绝对路径。"""
    return Artifact(name=path.name, sha256=_sha256(path), bytes=path.stat().st_size)


def _card(gate: str, *, license: License | None, **fields: Any) -> EvidenceCard:
    """构造证据卡，统一注入信封字段与默认许可。"""
    return EvidenceCard(
        card_id=_new_card_id(),
        created_at=_now(),
        gate=gate,
        license=license or License(),
        **fields,
    )


def _fingerprint_artifacts(rows: list[dict[str, Any]]) -> list[Artifact]:
    """把 metadata 里的输入指纹转成无路径的卡字段。"""
    return [
        Artifact(
            name=str(row["path"]).replace("\\", "/").rsplit("/", 1)[-1],
            sha256=str(row["sha256"]),
            bytes=int(row["bytes"]),
        )
        for row in rows
        if row.get("path") and row.get("sha256") and row.get("bytes") is not None
    ]


def _result_artifacts(result: dict[str, Any]) -> list[Artifact]:
    """把 attempt 结果里的文件身份转成无路径的卡字段。"""
    return [
        Artifact(
            name=str(name),
            sha256=str(item["sha256"]),
            bytes=int(item["bytes"]),
        )
        for name, item in (result.get("artifacts") or {}).items()
        if item.get("sha256") is not None and item.get("bytes") is not None
    ]


def _run(store: SessionStore, session_id: str, run_id: str) -> RunSnapshot:
    """取权威 run；缺失直接拒绝生成。"""
    run = store.get_qc_run(session_id, run_id)
    if run is None:
        raise ValueError("QC run 不存在")
    return run


def _qc_metadata(run: Mapping[str, Any]) -> dict[str, Any]:
    """读取 QC 本地 metadata；缺失时返回空表。"""
    path = Path(run["directory"]) / "metadata.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def build_qc_card(
    store: SessionStore,
    session_id: str,
    run_id: str,
    *,
    license: License | None = None,
) -> EvidenceCard:
    """QC 阈值复核卡：只含聚合统计、阈值与产物哈希，支持拒绝。"""
    run = _run(store, session_id, run_id)
    reviewed = [
        preview for preview in run.previews if preview["status"] in ("approved", "rejected")
    ]
    if not reviewed:
        raise ValueError("QC 尚未人工复核，不能生成证据卡")
    preview = reviewed[-1]
    active_id = run.get("active_preview_id")
    if active_id and preview["preview_id"] != active_id:
        raise ValueError("QC 复核已被更新的预览替代，不能生成证据卡")
    outcome = "accepted" if preview["status"] == "approved" else "rejected"
    reviewed_at = (
        preview.get("approved_at") if outcome == "accepted" else preview.get("rejected_at")
    )
    evidence = run["evidence"]
    # run["evidence"] 由 metadata + summary 组成；逐细胞汇总在 summary 下。
    qc_evidence = evidence["summary"]
    preview_summary = preview["summary"]
    directory = Path(run["directory"])
    card = _card(
        "qc_review",
        license=license,
        outcome=outcome,
        subject=Subject(
            session_id=session_id,
            run_id=run_id,
            binding_sha256=preview["binding_sha256"],
            source_revision=int(preview.get("source_revision", run["revision"])),
        ),
        method=Method(
            tool="sc_qc",
            tool_version=evidence["tool_version"],
            parameters=preview["params"],
            environment={
                "versions": "; ".join(qc_evidence["versions"]),
                "threshold_schema": preview["schema"],
                "preview_tool_version": preview["tool_version"],
            },
        ),
        observation={
            "cells": qc_evidence["cells"],
            "features": qc_evidence["features"],
            "zero_count_cells": qc_evidence["zero_count_cells"],
            "mt_symbol_resolved": qc_evidence["mt_symbol_resolved"],
            "metrics": qc_evidence["metrics"],
            "kept": preview_summary["kept"],
            "excluded": preview_summary["excluded"],
            "excluded_percent": preview_summary["excluded_percent"],
            "reason_counts": preview_summary["reason_counts"],
        },
        claim=Claim(
            text=(
                f"QC 阈值经人工批准：保留 {preview_summary['kept']}/{preview_summary['total']} 个细胞。"
                if outcome == "accepted"
                else "QC 阈值预览未获批准；未执行过滤或下游分析。"
            ),
            basis="fact",
            confidence="high",
        ),
        limitations=list(qc_evidence["warnings"])
        + ([] if outcome == "accepted" else ["该选择被拒绝，不用于下游分析。"]),
        provenance=Provenance(
            inputs=_fingerprint_artifacts(_qc_metadata(run).get("inputs_before") or []),
            artifacts=[
                _file_artifact(directory / name)
                for name in ("summary.json", "versions.txt", "qc.png")
                if (directory / name).is_file()
            ],
        ),
        review=Review(
            status=outcome,
            reviewer=preview.get("reviewer"),
            reason=preview.get("reason"),
            reviewed_at=reviewed_at,
        ),
    )
    return validate_card(card)


def build_cluster_card(
    store: SessionStore,
    session_id: str,
    run_id: str,
    *,
    attempt_id: str | None = None,
    license: License | None = None,
) -> EvidenceCard:
    """聚类复核卡：只含簇大小与版本，不含 barcode/UMAP 坐标，支持拒绝。"""
    run = _run(store, session_id, run_id)
    reviews = [*run.cluster_reviews(), *run.cluster_rejections()]
    reviews = [
        review
        for review in reviews
        if attempt_id is None or review["cluster_attempt_id"] == attempt_id
    ]
    if not reviews:
        raise ValueError("聚类尚未人工复核，不能生成证据卡")
    latest = max(reviews, key=lambda review: review["source_revision"])
    attempt = run.attempt("cluster", latest["cluster_attempt_id"])
    if attempt is None:
        raise ValueError("聚类结果缺失")
    facts = cluster_review_facts(run, attempt, Path(run["directory"]).resolve().parent)
    review, result = facts.current, facts.result
    if review is None or result is None:
        raise ValueError("聚类复核已失效，不能生成证据卡")
    outcome = review["outcome"]
    card = _card(
        "cluster_review",
        license=license,
        outcome=outcome,
        subject=Subject(
            session_id=session_id,
            run_id=run_id,
            attempt_id=attempt["attempt_id"],
            parent_attempt_id=attempt["pca_attempt_id"],
            binding_sha256=review["binding_sha256"],
            source_revision=review["source_revision"],
        ),
        method=Method(
            tool="sc_cluster",
            tool_version=attempt["tool_version"],
            protocol=json.loads(attempt["protocol"]),
            environment={"versions": "; ".join(result.get("versions") or [])},
        ),
        observation={
            "cells": result.get("cells"),
            "dims": result.get("dims"),
            "n_epochs": result.get("n_epochs"),
            "clusters": result.get("clusters"),
            "cluster_sizes": result.get("cluster_sizes"),
            "seurat_threads": result.get("seurat_threads"),
        },
        claim=Claim(
            text=(
                f"人工接受 {result.get('clusters')} 个探索性聚类（dims={result.get('dims')}）。"
                if outcome == "accepted"
                else "人工拒绝该探索性聚类结果；不用于下游分析。"
            ),
            basis="fact",
            confidence="medium",
        ),
        limitations=["聚类为探索性标签，不代表细胞类型注释。"]
        + ([] if outcome == "accepted" else ["人工拒绝，结果不得用于下游分析。"]),
        provenance=Provenance(artifacts=_result_artifacts(result)),
        review=Review(
            status=outcome,
            reviewer=review.get("reviewer"),
            reason=review.get("reason"),
            reviewed_at=review.get("created_at"),
        ),
    )
    return validate_card(card)


def build_pca_dimensions_card(
    store: SessionStore,
    session_id: str,
    run_id: str,
    *,
    attempt_id: str | None = None,
    license: License | None = None,
) -> EvidenceCard:
    """PCA 维度选择卡：绑定人工前缀与 PCA 产物身份，不含嵌入坐标。"""
    run = _run(store, session_id, run_id)
    reviews = [
        review
        for review in run.dimension_reviews()
        if attempt_id is None or review["pca_attempt_id"] == attempt_id
    ]
    if not reviews:
        raise ValueError("PCA 尚未人工记录维度，不能生成证据卡")
    review = reviews[-1]
    attempt = run.attempt("pca", review["pca_attempt_id"])
    if attempt is None or attempt["status"] != "completed" or not attempt.get("result"):
        raise ValueError("PCA 结果缺失")
    parent = run.attempt("filter", attempt["filter_attempt_id"])
    if parent is None:
        raise ValueError("PCA 父过滤缺失")
    result = attempt["result"]
    binding = dimension_binding(
        run,
        parent,
        attempt,
        {"artifacts": result.get("artifacts") or {}},
        DIMENSION_RECORD_VERSION,
    )
    if current_dimensions(run, attempt, binding, DIMENSION_RECORD_VERSION) != review:
        raise ValueError("维度记录与 PCA 身份不符")
    dims = review["dims"]
    card = _card(
        "pca_dimensions",
        license=license,
        outcome="accepted",
        subject=Subject(
            session_id=session_id,
            run_id=run_id,
            attempt_id=attempt["attempt_id"],
            parent_attempt_id=attempt["filter_attempt_id"],
            binding_sha256=review["binding_sha256"],
            source_revision=review["source_revision"],
        ),
        method=Method(
            tool="sc_pca",
            tool_version=attempt["tool_version"],
            protocol=json.loads(attempt["protocol"]),
            environment={"versions": "; ".join(result.get("versions") or [])},
        ),
        observation={
            "cells": result.get("cells"),
            "features": result.get("features"),
            "selected_features": result.get("selected_features"),
            "usable_features": result.get("usable_features"),
            "actual_pcs": result.get("actual_pcs"),
            "requested_pcs_max": result.get("requested_pcs_max"),
            "pc_reduction_reason": result.get("pc_reduction_reason"),
            "n_dims": len(dims),
            "dims": dims,
        },
        claim=Claim(
            text=(
                f"人工选择前 {len(dims)} 个 PC（共计算 {result.get('actual_pcs')} 个），"
                "用于下游聚类。"
            ),
            basis="fact",
            confidence="medium",
        ),
        limitations=["维度选择为探索性记录，不代表科学认可。"],
        provenance=Provenance(artifacts=_result_artifacts(result)),
        review=Review(
            status="accepted",
            reason=review.get("reason"),
            reviewed_at=review.get("created_at"),
        ),
    )
    return validate_card(card)


def build_marker_card(
    store: SessionStore,
    session_id: str,
    run_id: str,
    *,
    attempt_id: str | None = None,
    license: License | None = None,
) -> EvidenceCard:
    """marker 复核卡：只含聚合统计与候选总数，不含逐细胞表达。"""
    run = _run(store, session_id, run_id)
    reviews = [
        review
        for review in run.marker_reviews()
        if attempt_id is None or review["marker_attempt_id"] == attempt_id
    ]
    if not reviews:
        raise ValueError("marker 尚未人工复核，不能生成证据卡")
    review = reviews[-1]
    attempt = run.attempt("marker", review["marker_attempt_id"])
    if attempt is None or attempt["status"] != "completed" or not attempt.get("result"):
        raise ValueError("marker 结果缺失")
    facts = marker_review_facts(run, attempt, Path(run["directory"]).resolve().parent)
    if facts.current is None:
        raise ValueError("marker 复核已失效，不能生成证据卡")
    review = facts.current
    result = facts.result
    outcome = review["decision"]
    card = _card(
        "marker_review",
        license=license,
        outcome=outcome,
        subject=Subject(
            session_id=session_id,
            run_id=run_id,
            attempt_id=attempt["attempt_id"],
            parent_attempt_id=attempt["cluster_attempt_id"],
            binding_sha256=review["binding_sha256"],
            source_revision=review["source_revision"],
        ),
        method=Method(
            tool="sc_markers",
            tool_version=attempt["tool_version"],
            protocol=json.loads(attempt["protocol"]),
            parameters={"backend": result.get("backend")},
            environment={"versions": "; ".join(result.get("versions") or [])},
        ),
        observation={
            "cells": result.get("cells"),
            "features": result.get("features"),
            "clusters": result.get("clusters"),
            "marker_rows": result.get("marker_rows"),
            "positive_rows": result.get("positive_rows"),
            "negative_rows": result.get("negative_rows"),
        },
        claim=Claim(
            text=(
                f"人工接受 {result.get('marker_rows')} 行 marker 候选"
                f"（{result.get('clusters')} 个探索性簇）。"
                if outcome == "accepted"
                else "人工拒绝 marker 结果；不用于下游分析。"
            ),
            basis="fact",
            confidence="medium",
        ),
        limitations=[
            "marker 仅为候选统计，不代表细胞类型注释。",
            "marker 允许每簇至少 3 个细胞；卡内不披露簇级规模。",
        ]
        + ([] if outcome == "accepted" else ["人工拒绝，结果不得用于下游分析。"]),
        provenance=Provenance(artifacts=_result_artifacts(result)),
        review=Review(
            status=outcome,
            reviewer=review.get("reviewer"),
            reason=review.get("reason"),
            reviewed_at=review.get("created_at"),
        ),
    )
    return validate_card(card)
