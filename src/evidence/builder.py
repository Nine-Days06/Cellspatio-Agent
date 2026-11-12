"""从本地 run/attempt 与人工复核事实生成证据卡。"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

_OUTCOME_FROM_DECISION = {"accepted": "accepted", "rejected": "rejected"}


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
            name=Path(str(row["path"])).name, sha256=str(row["sha256"]), bytes=int(row["bytes"])
        )
        for row in rows
        if row.get("path") and row.get("sha256") and row.get("bytes") is not None
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
    """QC 阈值批准卡：只含聚合统计、阈值与产物哈希。"""
    run = _run(store, session_id, run_id)
    approved = [preview for preview in run["previews"] if preview["status"] == "approved"]
    if not approved:
        raise ValueError("QC 尚未人工批准，不能生成证据卡")
    preview = approved[-1]
    evidence = run["evidence"]
    summary = preview["summary"]
    directory = Path(run["directory"])
    card = EvidenceCard(
        card_id=_new_card_id(),
        created_at=_now(),
        gate="qc_review",
        outcome="accepted",
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
                "versions": "; ".join(evidence["versions"]),
                "threshold_schema": preview["schema"],
                "preview_tool_version": preview["tool_version"],
            },
        ),
        observation={
            "cells": evidence["cells"],
            "features": evidence["features"],
            "zero_count_cells": evidence["zero_count_cells"],
            "mt_symbol_resolved": evidence["mt_symbol_resolved"],
            "metrics": evidence["metrics"],
            "kept": summary["kept"],
            "excluded": summary["excluded"],
            "excluded_percent": summary["excluded_percent"],
            "reason_counts": summary["reason_counts"],
        },
        claim=Claim(
            text=f"QC 阈值经人工批准：保留 {summary['kept']}/{summary['total']} 个细胞。",
            basis="fact",
            confidence="high",
        ),
        limitations=list(evidence["warnings"]),
        provenance=Provenance(
            inputs=_fingerprint_artifacts(_qc_metadata(run).get("inputs_before") or []),
            artifacts=[
                _file_artifact(directory / name)
                for name in ("summary.json", "versions.txt", "qc.png")
                if (directory / name).is_file()
            ],
        ),
        review=Review(
            status="accepted",
            reason=preview.get("reason"),
            reviewed_at=preview.get("approved_at"),
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
    """聚类接受卡：只含簇大小与版本，不含 barcode/UMAP 坐标。"""
    run = _run(store, session_id, run_id)
    reviews = [
        review
        for review in run["cluster_reviews"]
        if attempt_id is None or review["cluster_attempt_id"] == attempt_id
    ]
    if not reviews:
        raise ValueError("聚类尚未人工接受，不能生成证据卡")
    review = reviews[-1]
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
        outcome="accepted",
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
            text=f"人工接受 {result.get('clusters')} 个探索性聚类（dims={result.get('dims')}）。",
            basis="fact",
            confidence="medium",
        ),
        limitations=["聚类为探索性标签，不代表细胞类型注释。"],
        provenance=Provenance(
            artifacts=[
                Artifact(
                    name=str(name),
                    sha256=str(item["sha256"]),
                    bytes=int(item["bytes"]),
                )
                for name, item in (result.get("artifacts") or {}).items()
                if item.get("sha256") is not None and item.get("bytes") is not None
            ],
        ),
        review=Review(
            status="accepted",
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
        outcome=_OUTCOME_FROM_DECISION[review["decision"]],
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
            status=_OUTCOME_FROM_DECISION[review["decision"]],
            reviewer=review.get("reviewer"),
            reason=review.get("reason"),
            reviewed_at=review.get("created_at"),
        ),
        license=license or License(),
    )
    return validate_card(card)
