"""证据卡 schema、隐私校验与三类采集点。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.evidence.builder import build_cluster_card, build_qa_card, build_qc_card
from src.evidence.schema import EvidenceCard, validate_card

EXAMPLES = Path(__file__).resolve().parents[2] / "docs" / "examples" / "evidence-cards"


def test_example_cards_pass_validation():
    cards = sorted(EXAMPLES.glob("*.json"))
    assert {path.stem for path in cards} == {"qc", "cluster", "qa"}
    for path in cards:
        card = EvidenceCard.model_validate_json(path.read_text(encoding="utf-8"))
        assert validate_card(card).card_id == card.card_id


def _card() -> EvidenceCard:
    return EvidenceCard(
        card_id="evc-test",
        created_at="2026-11-12T00:00:00+00:00",
        gate="qc_review",
        outcome="accepted",
        subject={
            "session_id": "s",
            "run_id": "r",
            "binding_sha256": "a" * 64,
        },
        method={"tool": "sc_qc", "tool_version": "1"},
        observation={"cells": 10},
        claim={"text": "ok"},
        review={"status": "accepted"},
    )


def test_privacy_rejects_forbidden_keys():
    card = _card()
    card.observation["barcode"] = ["AAACCTG"]
    with pytest.raises(ValueError, match="禁止字段"):
        validate_card(card)


def test_privacy_rejects_absolute_paths():
    card = _card()
    card.claim.text = r"读取 D:\secret\matrix.mtx"
    with pytest.raises(ValueError, match="绝对路径"):
        validate_card(card)


def test_privacy_rejects_cell_level_lists():
    card = _card()
    card.observation["values"] = list(range(300))
    with pytest.raises(ValueError, match="列表过长"):
        validate_card(card)


def test_privacy_rejects_barcodes_and_sample_ids():
    card = _card()
    card.observation["example"] = "AAACCTGAGAAACCAT-1"
    with pytest.raises(ValueError, match="barcode"):
        validate_card(card)

    card = _card()
    card.review.reason = "样本 GSM1234567 复核通过"
    with pytest.raises(ValueError, match="样本级 ID"):
        validate_card(card)


def test_privacy_rejects_small_cell_aggregates():
    card = _card()
    card.observation["cells"] = 4
    with pytest.raises(ValueError, match="聚合阈值"):
        validate_card(card)


class _Store:
    """最小假 store：只暴露生成器用到的读取方法。"""

    def __init__(
        self,
        run: dict | None,
        messages: list | None = None,
        reviews: list | None = None,
    ):
        """保存预置的 run、消息与复核事实。"""
        self.run = run
        self.messages = messages or []
        self.reviews = reviews or []

    def get_qc_run(self, _session_id, _run_id):
        return self.run

    def get_messages(self, _session_id):
        return self.messages

    def get_knowledge_qa_reviews(self, _answer_id):
        return self.reviews


def test_build_qc_card_drops_input_paths(tmp_path):
    directory = tmp_path / "qc"
    directory.mkdir()
    (directory / "metadata.json").write_text(
        json.dumps(
            {"inputs_before": [{"path": r"C:\secret\matrix.mtx", "bytes": 10, "sha256": "b" * 64}]}
        ),
        encoding="utf-8",
    )
    (directory / "summary.json").write_text("{}", encoding="utf-8")
    (directory / "versions.txt").write_text("R 4.6\nSeurat 5\nMatrix 1\n", encoding="utf-8")
    (directory / "qc.png").write_bytes(b"png")
    run = {
        "session_id": "s",
        "run_id": "r",
        "directory": str(directory),
        "revision": 2,
        "evidence": {
            "tool_version": "sc-qc/1",
            "cells": 10,
            "features": 5,
            "zero_count_cells": 0,
            "mt_symbol_resolved": True,
            "warnings": [],
            "versions": ["R 4.6", "Seurat 5", "Matrix 1"],
            "metrics": {},
        },
        "previews": [
            {
                "status": "approved",
                "binding_sha256": "a" * 64,
                "params": {"min_features": 200},
                "schema": "qc-thresholds/1",
                "tool_version": "qc-preview/1",
                "summary": {
                    "kept": 9,
                    "excluded": 1,
                    "total": 10,
                    "excluded_percent": 10.0,
                    "reason_counts": {"min_features": 1},
                },
                "reason": "ok",
                "approved_at": "2026-11-12T00:00:00+00:00",
            }
        ],
    }

    card = build_qc_card(_Store(run), "s", "r")

    assert card.gate == "qc_review"
    assert [item.name for item in card.provenance.inputs] == ["matrix.mtx"]
    assert "secret" not in card.to_json()


def test_build_cluster_card_keeps_only_aggregates():
    result = {
        "cells": 11,
        "dims": 5,
        "n_epochs": 10,
        "clusters": 2,
        "cluster_sizes": [
            {"cluster_id": "0", "cells": 6, "proportion": 0.5455},
            {"cluster_id": "1", "cells": 5, "proportion": 0.4545},
        ],
        "seurat_threads": 1,
        "versions": ["R 4.6", "Seurat 5"],
        "artifacts": {
            "summary.json": {"path": r"C:\secret\summary.json", "sha256": "b" * 64, "bytes": 10}
        },
    }
    run = {
        "session_id": "s",
        "run_id": "r",
        "directory": "",
        "revision": 3,
        "evidence": {},
        "cluster_attempts": [
            {
                "attempt_id": "c1",
                "pca_attempt_id": "p1",
                "tool_version": "sc-cluster/1",
                "protocol": json.dumps({"algorithm": "louvain"}),
                "result": result,
            }
        ],
        "cluster_reviews": [
            {
                "cluster_attempt_id": "c1",
                "binding_sha256": "c" * 64,
                "source_revision": 2,
                "reason": "ok",
                "created_at": "2026-11-12T00:00:00+00:00",
            }
        ],
    }

    card = build_cluster_card(_Store(run), "s", "r")

    assert card.observation["cluster_sizes"][0]["cells"] == 6
    assert card.provenance.artifacts[0].name == "summary.json"
    assert "secret" not in card.to_json()


def test_build_qa_card_hashes_question():
    package = {
        "answer_id": "qa1",
        "question": "TP53 在肝癌中的作用？",
        "outcome": "supported_answer",
        "answer": "TP53 常作为抑癌基因失活。",
        "claims": [{"text": "x", "evidence_ids": ["E1"], "basis": "fact"}],
        "limitations": ["检索有限"],
        "evidence": [{"evidence_id": "E1"}],
        "sources": [{"pmid": "12345678"}],
        "config": {"lightrag_version": "1.0", "max_evidence": 8},
    }
    store = _Store(
        run=None,
        messages=[{"results": {"knowledge_qa": package}}],
        reviews=[
            {
                "decision": "accepted",
                "binding_sha256": "d" * 64,
                "source_revision": 0,
                "record_version": "knowledge-qa-review/1",
                "reason": "ok",
                "reviewer": "u",
                "created_at": "2026-11-12T00:00:00+00:00",
            }
        ],
    )

    card = build_qa_card(store, "s", "qa1")

    assert card.observation["question_chars"] == len(package["question"])
    assert card.provenance.source_ids == ["12345678"]
    assert card.claim.text == "x"
    assert package["question"] not in card.to_json()
