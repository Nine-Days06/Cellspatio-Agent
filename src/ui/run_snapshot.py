"""QC run 快照：九表聚合结果与阶段访问器。"""

from __future__ import annotations

from collections.abc import ItemsView, Iterator, Mapping
from typing import Any


class RunSnapshot(Mapping[str, Any]):
    """持有已解码的 run 字段，并以命名访问器隔离阶段表键名。"""

    def __init__(self, raw: dict[str, Any]):
        self._raw = raw

    def __getitem__(self, key: str) -> Any:
        return self._raw[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._raw)

    def __len__(self) -> int:
        return len(self._raw)

    def get(self, key: str, default: Any = None) -> Any:
        return self._raw.get(key, default)

    def items(self) -> ItemsView[str, Any]:
        return self._raw.items()

    @property
    def previews(self) -> list[dict[str, Any]]:
        return list(self._raw.get("previews") or [])

    @property
    def active_preview(self) -> dict[str, Any] | None:
        active_id = self._raw.get("active_preview_id")
        return next((p for p in self.previews if p["preview_id"] == active_id), None)

    @property
    def filter_attempts(self) -> list[dict[str, Any]]:
        return list(self._raw.get("attempts") or [])

    def pca_attempts(self, filter_attempt_id: str | None = None) -> list[dict[str, Any]]:
        return self._by(self._raw.get("pca_attempts") or [], "filter_attempt_id", filter_attempt_id)

    def dimension_reviews(self, pca_attempt_id: str | None = None) -> list[dict[str, Any]]:
        return self._by(self._raw.get("dimension_reviews") or [], "pca_attempt_id", pca_attempt_id)

    def cluster_attempts(self, pca_attempt_id: str | None = None) -> list[dict[str, Any]]:
        return self._by(self._raw.get("cluster_attempts") or [], "pca_attempt_id", pca_attempt_id)

    def cluster_reviews(self, cluster_attempt_id: str | None = None) -> list[dict[str, Any]]:
        return self._by(
            self._raw.get("cluster_reviews") or [], "cluster_attempt_id", cluster_attempt_id
        )

    def cluster_rejections(self, cluster_attempt_id: str | None = None) -> list[dict[str, Any]]:
        return self._by(
            self._raw.get("cluster_rejections") or [], "cluster_attempt_id", cluster_attempt_id
        )

    def marker_attempts(self, cluster_attempt_id: str | None = None) -> list[dict[str, Any]]:
        return self._by(
            self._raw.get("marker_attempts") or [], "cluster_attempt_id", cluster_attempt_id
        )

    def marker_reviews(self, marker_attempt_id: str | None = None) -> list[dict[str, Any]]:
        return self._by(
            self._raw.get("marker_reviews") or [], "marker_attempt_id", marker_attempt_id
        )

    @staticmethod
    def _by(items: list[dict[str, Any]], key: str, value: str | None) -> list[dict[str, Any]]:
        """按父 ID 过滤并返回副本；value 为空时返回全部。"""
        if value is None:
            return list(items)
        return [item for item in items if item[key] == value]
