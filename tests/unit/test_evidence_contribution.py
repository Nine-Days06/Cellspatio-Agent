"""票据 18：本地预览 → 显式贡献 → 隔离区 → 复核 → 发布 → 检索 → 撤销。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from src.cli import evidence_governance
from src.evidence.contribution import build_envelope, preview_card
from src.evidence.governance import (
    ACTION_COMPLIANCE_PASS,
    ACTION_DOMAIN_PASS,
    COMPLIANCE_CHECKLIST,
    DOMAIN_CHECKLIST,
    ROLE_COMPLIANCE,
    ROLE_CONTRIBUTOR,
    ROLE_DOMAIN,
    GovernanceStore,
)
from src.evidence.schema import EvidenceCard, License

EXAMPLES = Path(__file__).resolve().parents[2] / "docs" / "examples" / "evidence-cards"


def _example_card() -> EvidenceCard:
    """读取票据 16 的 QC 示例卡。"""
    return EvidenceCard.model_validate_json((EXAMPLES / "qc.json").read_text(encoding="utf-8"))


def _checks(names: tuple[str, ...]) -> dict[str, bool]:
    """把清单名全部置 true。"""
    return {name: True for name in names}


def _run_loop(root: Path) -> dict:
    """跑完整闭环并返回关键结果。"""
    store = GovernanceStore(root / "public_evidence")
    preview = preview_card(_example_card(), contributor_id="alice")
    envelope = build_envelope(preview, consent=True)
    submitted = store.submit(envelope)
    sid = submitted["evidence_id"]

    store.review(
        sid,
        actor_id="dora",
        role=ROLE_DOMAIN,
        action=ACTION_DOMAIN_PASS,
        checklist=_checks(DOMAIN_CHECKLIST),
    )
    published = store.review(
        sid,
        actor_id="carl",
        role=ROLE_COMPLIANCE,
        action=ACTION_COMPLIANCE_PASS,
        checklist=_checks(COMPLIANCE_CHECKLIST),
    )
    hits = store.search("QC")
    store.withdraw(sid, actor_id="alice", role=ROLE_CONTRIBUTOR, reason="演示撤销")
    return {
        "sid": sid,
        "submitted": submitted,
        "published": published,
        "hits": hits,
        "after_withdraw": store.search(),
        "events": [event["action"] for event in store.events(sid)],
        "tombstone": store.get(sid),
    }


def test_preview_only_contains_card_fields():
    preview = preview_card(_example_card(), contributor_id="alice")

    assert preview["privacy"] == "passed"
    assert preview["admission"]["status"] == "passed"
    assert preview["will_send"]["card"]["card_id"] == "evc-example-qc"
    assert preview["will_send"]["submission"]["contributor_id"] == "alice"
    assert preview["will_send"]["submission"]["consent"] is False
    assert preview["consent_text"]
    assert "原始矩阵与逐细胞数据" in preview["not_sent"]
    sent = json.dumps(preview["will_send"], ensure_ascii=False)
    assert "C:\\" not in sent
    assert "barcode" not in sent
    assert "counts_matrix" not in sent


def test_preview_rejects_raw_paths():
    card = _example_card()
    card.claim.text = r"读取 D:\secret\matrix.mtx"

    with pytest.raises(ValueError, match="绝对路径"):
        preview_card(card, contributor_id="alice")


def test_preview_marks_admission_fail_without_license():
    card = _example_card()
    card.license = License()

    preview = preview_card(card, contributor_id="alice")

    assert preview["admission"]["status"] == "failed"
    assert any("license.spdx" in item for item in preview["admission"]["problems"])


def test_contribution_requires_explicit_consent():
    preview = preview_card(_example_card(), contributor_id="alice")

    with pytest.raises(ValueError, match="显式同意"):
        build_envelope(preview, consent=False)


def test_tampered_preview_is_rejected():
    preview = preview_card(_example_card(), contributor_id="alice")
    tampered = copy.deepcopy(preview)
    tampered["will_send"]["card"]["claim"]["text"] = "被篡改的结论"

    with pytest.raises(ValueError, match="预览内容已被修改"):
        build_envelope(tampered, consent=True)


def test_end_to_end_loop_is_rerunnable(tmp_path):
    first = _run_loop(tmp_path / "run1")
    second = _run_loop(tmp_path / "run2")

    assert first["submitted"]["status"] == "pending"
    assert first["published"]["status"] == "accepted"
    assert [item["evidence_id"] for item in first["hits"]] == [first["sid"]]
    assert first["hits"][0]["claim"].startswith("QC 阈值")
    assert first["after_withdraw"] == []
    assert first["tombstone"]["status"] == "withdrawn"
    assert first["tombstone"]["reason"] == "演示撤销"
    assert "card" not in first["tombstone"]
    assert first["events"] == ["submit", "domain_pass", "compliance_pass", "withdraw"]

    assert second["published"]["status"] == "accepted"
    assert second["after_withdraw"] == []
    assert second["events"] == first["events"]


def test_cli_preview_and_contribute(tmp_path, capsys):
    card_path = EXAMPLES / "qc.json"
    preview_path = tmp_path / "preview.json"

    assert (
        evidence_governance.main(
            [
                "--root",
                str(tmp_path),
                "preview",
                str(card_path),
                "--actor",
                "alice",
                "--out",
                str(preview_path),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["privacy"] == "passed"
    assert preview_path.is_file()

    assert (
        evidence_governance.main(
            [
                "--root",
                str(tmp_path),
                "contribute",
                str(preview_path),
                "--consent",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "pending"

    assert evidence_governance.main(["--root", str(tmp_path), "contribute", str(preview_path)]) == 2
    assert "显式同意" in json.loads(capsys.readouterr().out)["error"]
