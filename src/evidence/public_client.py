"""本地客户端到公共证据服务的 HTTP 封装。

只发送预览中的聚合字段；token 由调用方提供，不落盘。
"""

from __future__ import annotations

from typing import Any

import httpx


class PublicEvidenceClient:
    """公共证据服务的最小客户端。"""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout: float = 30.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        """绑定服务地址与可选 token。"""
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._client = http_client or httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        """关闭底层连接。"""
        self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        auth: bool = True,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """发请求并解析 JSON；非 2xx 统一抛 ValueError(detail)。"""
        headers = {"Authorization": f"Bearer {self.token}"} if auth and self.token else {}
        try:
            response = self._client.request(method, path, json=json, headers=headers, params=params)
        except httpx.HTTPError as exc:
            raise ValueError(f"连接公共证据服务失败：{exc}") from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {"detail": response.text}
        if response.status_code >= 400:
            raise ValueError(str(payload.get("detail") or response.status_code))
        return payload

    def login(self, username: str, password: str) -> dict[str, Any]:
        """登录并把 token 保存在客户端上。"""
        payload = self._request(
            "POST",
            "/api/auth/login",
            json={"username": username, "password": password},
            auth=False,
        )
        self.token = payload["token"]
        return payload

    def upload(self, preview: dict[str, Any], *, consent: bool) -> dict[str, Any]:
        """上传预览内容并显式同意。"""
        return self._request(
            "POST",
            "/api/public/evidence/submissions",
            json={"preview": preview, "consent": consent},
        )

    def search(self, query: str = "", gate: str | None = None) -> list[dict[str, Any]]:
        """公开检索 accepted 证据。"""
        params = {"q": query}
        if gate:
            params["gate"] = gate
        payload = self._request("GET", "/api/public/evidence/search", params=params, auth=False)
        return list(payload.get("results") or [])

    def citation(self, evidence_id: str) -> dict[str, Any]:
        """读取引用索引条目。"""
        return self._request("GET", f"/api/public/evidence/{evidence_id}/citation", auth=False)
