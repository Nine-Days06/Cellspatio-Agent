"""复核词汇：gate、状态、动作、角色与清单的唯一中文标签来源。"""

from __future__ import annotations

GATE_LABELS = {
    "qc_review": "质控复核",
    "pca_dimensions": "PCA 维度",
    "cluster_review": "聚类复核",
    "marker_review": "Marker 复核",
    "knowledge_qa_review": "知识问答复核",
}

STATUS_LABELS = {
    "pending": "待审",
    "needs_changes": "需补充",
    "accepted": "已接受",
    "rejected": "已拒绝",
    "withdrawn": "已撤回",
}

ACTION_LABELS = {
    "submit": "提交",
    "domain_pass": "领域通过",
    "compliance_pass": "合规通过",
    "domain_needs_changes": "领域要求补充",
    "compliance_needs_changes": "合规要求补充",
    "domain_reject": "领域拒绝",
    "compliance_reject": "合规拒绝",
    "withdraw": "撤销",
}

ROLE_LABELS = {
    "contributor": "贡献者",
    "domain_reviewer": "领域复核者",
    "compliance_reviewer": "合规复核者",
}

CHECK_LABELS = {
    "method_complete": "方法与参数完整",
    "claim_matches_observation": "结论与观测一致",
    "limitations_present": "已写明限制",
    "privacy_scan_passed": "隐私扫描通过",
    "license_and_attribution_present": "许可与署名完整",
    "no_raw_data": "不含原始数据或逐细胞信息",
    "small_sample_ok": "小样本已作说明",
}


def vocabulary() -> dict[str, dict[str, str]]:
    """返回前端查表所需的全部标签；每次返回副本，避免调用方修改模块常量。"""
    return {
        "gates": dict(GATE_LABELS),
        "statuses": dict(STATUS_LABELS),
        "actions": dict(ACTION_LABELS),
        "roles": dict(ROLE_LABELS),
        "checklists": dict(CHECK_LABELS),
    }
