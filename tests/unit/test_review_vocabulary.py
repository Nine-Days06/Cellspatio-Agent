"""复核词汇：标签键必须覆盖协议全部合法值，端点无需登录即可读取。"""

from __future__ import annotations

from typing import get_args

from fastapi.testclient import TestClient

from src.evidence import review_protocol
from src.evidence.schema import Gate, Outcome
from src.evidence.vocabulary import vocabulary
from src.public_api.app import create_public_app


def _protocol_values(prefix: str) -> set[str]:
    """模块中以指定前缀声明的字符串常量集合。"""
    return {
        value
        for name, value in vars(review_protocol).items()
        if name.startswith(prefix) and isinstance(value, str)
    }


def test_vocabulary_covers_all_protocol_values():
    """漏掉任一 gate、状态、动作、角色或清单键都应失败。"""
    labels = vocabulary()
    assert set(labels) == {"gates", "statuses", "actions", "roles", "checklists"}
    assert set(labels["gates"]) == set(get_args(Gate))
    assert set(labels["statuses"]) == set(get_args(Outcome)) | _protocol_values("STATE_")
    assert set(labels["actions"]) == _protocol_values("ACTION_")
    assert set(labels["roles"]) == _protocol_values("ROLE_")
    assert set(labels["checklists"]) == set(review_protocol.DOMAIN_CHECKLIST) | set(
        review_protocol.COMPLIANCE_CHECKLIST
    )
    assert all(
        isinstance(value, str) and value for group in labels.values() for value in group.values()
    )


def test_vocabulary_endpoint_is_public(tmp_path):
    """无需 token 返回与 vocabulary() 一致的五组标签。"""
    client = TestClient(create_public_app(tmp_path))
    response = client.get("/api/public/vocabulary")
    assert response.status_code == 200
    assert response.json() == vocabulary()
