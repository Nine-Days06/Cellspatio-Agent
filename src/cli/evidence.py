"""CLI: 生成与校验本地私有证据卡。

python -m src.cli.evidence export qc <session_id> <run_id>
python -m src.cli.evidence export cluster <session_id> <run_id> [--attempt <id>]
python -m src.cli.evidence export pca <session_id> <run_id> [--attempt <id>]
python -m src.cli.evidence export marker <session_id> <run_id> [--attempt <id>]
python -m src.cli.evidence export qa <session_id> <answer_id>
python -m src.cli.evidence review qc|cluster|marker <session_id> <run_id> <target_id> --reason ...
python -m src.cli.evidence validate <card.json>
python -m src.cli.evidence schema
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from src.analysis.sc_marker_reviews import CLUSTER_RECORD_VERSION
from src.analysis.sc_markers import MARKER_REVIEW_VERSION
from src.evidence.builder import (
    build_cluster_card,
    build_marker_card,
    build_pca_dimensions_card,
    build_qa_card,
    build_qc_card,
)
from src.evidence.schema import SCHEMA_VERSION, EvidenceCard, License, json_schema, validate_card
from src.ui.session_store import SessionStore


def _store(base: Path) -> SessionStore:
    return SessionStore(base / ".cellspatio" / "sessions.db")


def _write(card: EvidenceCard, base: Path, out: str | None) -> Path:
    path = Path(out) if out else base / "data" / "evidence" / f"{card.card_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(card.to_json(), encoding="utf-8")
    return path


def export_card(
    kind: str,
    session_id: str,
    target_id: str,
    *,
    attempt_id: str | None = None,
    out: str | None = None,
    spdx: str | None = None,
    attribution: str | None = None,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """按 gate 生成一张卡并落盘；只写 `<data>/evidence/` 或显式 --out。"""
    base = Path(root or Path.cwd()).resolve()
    store = _store(base)
    license = License(spdx=spdx, attribution=attribution)
    if kind == "qc":
        card = build_qc_card(store, session_id, target_id, license=license)
    elif kind == "cluster":
        card = build_cluster_card(
            store, session_id, target_id, attempt_id=attempt_id, license=license
        )
    elif kind == "pca":
        card = build_pca_dimensions_card(
            store, session_id, target_id, attempt_id=attempt_id, license=license
        )
    elif kind == "marker":
        card = build_marker_card(
            store, session_id, target_id, attempt_id=attempt_id, license=license
        )
    elif kind == "qa":
        card = build_qa_card(store, session_id, target_id, license=license)
    else:
        raise ValueError("kind 必须是 qc、cluster、pca、marker 或 qa")
    path = _write(card, base, out)
    return {"status": "ok", "card_id": card.card_id, "gate": card.gate, "path": str(path)}


def record_review(
    kind: str,
    session_id: str,
    run_id: str,
    target_id: str,
    *,
    reason: str,
    reviewer: str = "local-user",
    decision: str | None = None,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """记录 QC/聚类拒绝或 marker 人工复核；只落本地事实，不触发执行。"""
    store = _store(Path(root or Path.cwd()).resolve())
    run = store.get_qc_run(session_id, run_id)
    if run is None:
        raise ValueError("QC run 不存在")
    if kind != "marker" and decision is not None:
        raise ValueError("--decision 只用于 marker 复核")
    if kind == "qc":
        store.record_qc_rejection(session_id, run_id, target_id, reason, reviewer)
    elif kind == "cluster":
        cluster = next(
            (item for item in run["cluster_attempts"] if item["attempt_id"] == target_id),
            None,
        )
        if cluster is None:
            raise ValueError("聚类关联不存在")
        store.record_sc_cluster_rejection(
            session_id,
            run,
            cluster,
            cluster["binding_sha256"],
            CLUSTER_RECORD_VERSION,
            run["revision"],
            reason,
            reviewer,
        )
    elif kind == "marker":
        if decision not in {"accepted", "rejected"}:
            raise ValueError("marker 复核必须指定 --decision accepted|rejected")
        store.record_sc_marker_review(
            session_id,
            target_id,
            decision,
            reason,
            reviewer,
            MARKER_REVIEW_VERSION,
            revision=run["revision"],
        )
    else:
        raise ValueError("kind 必须是 qc、cluster 或 marker")
    return {"status": "ok", "kind": kind, "target_id": target_id}


def validate_file(path: str | Path) -> dict[str, Any]:
    """读取并校验一张已有证据卡。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"证据卡 schema_version 必须是 {SCHEMA_VERSION}")
    card = EvidenceCard.model_validate(data)
    validate_card(card)
    return {"status": "ok", "card_id": card.card_id, "gate": card.gate}


def main(argv: list[str] | None = None) -> int:
    """解析参数并执行 export/validate/schema。"""
    parser = argparse.ArgumentParser(description="生成或校验 CellSpatio 本地证据卡")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("kind", choices=("qc", "cluster", "pca", "marker", "qa"))
    export_parser.add_argument("session_id")
    export_parser.add_argument("target_id", help="qc/pca/marker 为 run_id；qa 为 answer_id")
    export_parser.add_argument("--attempt", default=None, help="聚类/PCA/marker attempt_id")
    export_parser.add_argument("--out", default=None)
    export_parser.add_argument("--root", default=None, help="数据目录（默认 cwd）")
    export_parser.add_argument("--license", default=None, help="SPDX 许可，如 CC-BY-4.0")
    export_parser.add_argument("--attribution", default=None, help="署名")

    review_parser = subparsers.add_parser("review")
    review_parser.add_argument("kind", choices=("qc", "cluster", "marker"))
    review_parser.add_argument("session_id")
    review_parser.add_argument("run_id")
    review_parser.add_argument(
        "target_id", help="QC preview_id / 聚类 attempt_id / marker attempt_id"
    )
    review_parser.add_argument("--reason", required=True)
    review_parser.add_argument("--reviewer", default="local-user")
    review_parser.add_argument("--decision", choices=("accepted", "rejected"), default=None)
    review_parser.add_argument("--root", default=None, help="数据目录（默认 cwd）")

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("path")

    subparsers.add_parser("schema")

    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            result = export_card(
                args.kind,
                args.session_id,
                args.target_id,
                attempt_id=args.attempt,
                out=args.out,
                spdx=args.license,
                attribution=args.attribution,
                root=args.root,
            )
        elif args.command == "validate":
            result = validate_file(args.path)
        elif args.command == "review":
            result = record_review(
                args.kind,
                args.session_id,
                args.run_id,
                args.target_id,
                reason=args.reason,
                reviewer=args.reviewer,
                decision=args.decision,
                root=args.root,
            )
        else:
            result = json_schema()
    except (OSError, ValueError, sqlite3.Error, KeyError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
