"""从本地 run/attempt 与人工复核事实生成证据卡。"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.analysis.sc_pca import DIMENSION_RECORD_VERSION, dimension_binding
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


def _run(store: SessionStore, session_id: str, run_id: str) -> dict[str, Any]:
    """取权威 run；缺失直接拒绝生成。"""
    run = store.get_qc_run(session_id, run_id)
    if run is None:
        raise ValueError("QC run 不存在")
    return run


def _qc_metadata(run: dict[str, Any]) -> dict[str, Any]:
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
        preview for preview in run["previews"] if preview["status"] in ("approved", "rejected")
    ]
    if not reviewed:
        raise ValueError("QC 尚未人工复核，不能生成证据卡")
    preview = reviewed[-1]
    outcome = "accepted" if preview["status"] == "approved" else "rejected"
    reviewed_at = (
        preview.get("approved_at") if outcome == "accepted" else preview.get("rejected_at")
    )
    evidence = run["evidence"]
    # run["evidence"] 由 metadata + summary 组成；逐细胞汇总在 summary 下。
    qc_evidence = evidence["summary"]
    preview_summary = preview["summary"]
    directory = Path(run["directory"])
    card = EvidenceCard(
        card_id=_new_card_id(),
        created_at=_now(),
        gate="qc_review",
        outcome=outcome,
        subject=Subject(
            session_id=session_id,
            run_id=run_id,
            binding_sha256=preview["binding_sha256"],
            source_revision=run["revision"],
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
        license=license or License(),
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
    facts = [{**review, "outcome": "accepted"} for review in run["cluster_reviews"]] + [
        {**review, "outcome": "rejected"} for review in run.get("cluster_rejections", [])
    ]
    facts = [
        fact for fact in facts if attempt_id is None or fact["cluster_attempt_id"] == attempt_id
    ]
    if not facts:
        raise ValueError("聚类尚未人工复核，不能生成证据卡")
    facts.sort(key=lambda fact: (fact["created_at"], fact.get("review_id", "")))
    review = facts[-1]
    outcome = review["outcome"]
    attempt = next(
        (
            item
            for item in run["cluster_attempts"]
            if item["attempt_id"] == review["cluster_attempt_id"]
        ),
        None,
    )
    if attempt is None or not attempt.get("result"):
        raise ValueError("聚类结果缺失")
    result = attempt["result"]
    card = EvidenceCard(
        card_id=_new_card_id(),
        created_at=_now(),
        gate="cluster_review",
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
        license=license or License(),
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
        for review in run["dimension_reviews"]
        if attempt_id is None or review["pca_attempt_id"] == attempt_id
    ]
    if not reviews:
        raise ValueError("PCA 尚未人工记录维度，不能生成证据卡")
    review = reviews[-1]
    attempt = next(
        (item for item in run["pca_attempts"] if item["attempt_id"] == review["pca_attempt_id"]),
        None,
    )
    if attempt is None or attempt["status"] != "completed" or not attempt.get("result"):
        raise ValueError("PCA 结果缺失")
    parent = next(
        (item for item in run["attempts"] if item["attempt_id"] == attempt["filter_attempt_id"]),
        None,
    )
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
    if review["binding_sha256"] != binding or review["record_version"] != DIMENSION_RECORD_VERSION:
        raise ValueError("维度记录与 PCA 身份不符")
    dims = review["dims"]
    card = EvidenceCard(
        card_id=_new_card_id(),
        created_at=_now(),
        gate="pca_dimensions",
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
        license=license or License(),
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
        for review in run.get("marker_reviews", [])
        if attempt_id is None or review["marker_attempt_id"] == attempt_id
    ]
    if not reviews:
        raise ValueError("marker 尚未人工复核，不能生成证据卡")
    review = reviews[-1]
    attempt = next(
        (
            item
            for item in run["marker_attempts"]
            if item["attempt_id"] == review["marker_attempt_id"]
        ),
        None,
    )
    if attempt is None or attempt["status"] != "completed" or not attempt.get("result"):
        raise ValueError("marker 结果缺失")
    if review["binding_sha256"] != attempt["binding_sha256"]:
        raise ValueError("marker 复核与结果身份不符")
    result = attempt["result"]
    outcome = review["decision"]
    card = EvidenceCard(
        card_id=_new_card_id(),
        created_at=_now(),
        gate="marker_review",
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
        license=license or License(),
    )
    return validate_card(card)


def _qa_package(store: SessionStore, session_id: str, answer_id: str) -> dict[str, Any]:
    for message in store.get_messages(session_id):
        package = (message.get("results") or {}).get("knowledge_qa")
        if package and package.get("answer_id") == answer_id:
            return package
    raise ValueError("找不到问答包")


def build_qa_card(
    store: SessionStore,
    session_id: str,
    answer_id: str,
    *,
    license: License | None = None,
) -> EvidenceCard:
    """知识问答复核卡：问题只留哈希与长度，证据只留来源 ID。"""
    reviews = store.get_knowledge_qa_reviews(answer_id)
    if not reviews:
        raise ValueError("知识问答尚未人工复核，不能生成证据卡")
    review = reviews[-1]
    package = _qa_package(store, session_id, answer_id)
    config = package.get("config") or {}
    claims = package.get("claims") or []
    # 结论只用结构化 claims，避免答案复述问题时泄漏原文
    claim_text = "; ".join(str(item.get("text")) for item in claims if item.get("text"))
    claim_text = claim_text or f"问答结论：{package.get('outcome')}"
    source_ids = [
        str(item.get("pmid") or item.get("doi") or item.get("source_id"))
        for item in package.get("sources") or []
        if item.get("pmid") or item.get("doi") or item.get("source_id")
    ]
    card = EvidenceCard(
        card_id=_new_card_id(),
        created_at=_now(),
        gate="knowledge_qa_review",
        outcome=review["decision"],
        subject=Subject(
            session_id=session_id,
            run_id=answer_id,
            binding_sha256=review["binding_sha256"],
            source_revision=review["source_revision"],
        ),
        method=Method(
            tool="knowledge_qa",
            tool_version=str(config.get("lightrag_version") or "unknown"),
            protocol={"record_version": review["record_version"]},
            parameters={
                key: config[key]
                for key in ("max_evidence", "max_evidence_chars", "workspace")
                if key in config
            },
            environment={"untraceable_chunks": str(config.get("untraceable_chunks", 0))},
        ),
        observation={
            "outcome": package.get("outcome"),
            "claims": len(package.get("claims") or []),
            "evidence": len(package.get("evidence") or []),
            "sources": len(package.get("sources") or []),
            "question_sha256": hashlib.sha256(
                str(package.get("question") or "").encode("utf-8")
            ).hexdigest(),
            "question_chars": len(str(package.get("question") or "")),
        },
        claim=Claim(
            text=claim_text,
            basis="inference",
            confidence="medium" if package.get("outcome") == "supported_answer" else "low",
        ),
        limitations=list(package.get("limitations") or []),
        provenance=Provenance(source_ids=source_ids),
        review=Review(
            status=review["decision"],
            reviewer=review.get("reviewer"),
            reason=review.get("reason"),
            reviewed_at=review.get("created_at"),
        ),
        license=license or License(),
    )
    return validate_card(card)
