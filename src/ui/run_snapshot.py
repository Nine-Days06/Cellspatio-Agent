"""QC run 快照：九表聚合结果与阶段访问器。"""

from __future__ import annotations

from collections.abc import ItemsView, Iterator, Mapping
from typing import Any

_STAGE_KEYS = {
    "filter": "attempts",
    "pca": "pca_attempts",
    "cluster": "cluster_attempts",
    "marker": "marker_attempts",
}
_STAGE_PARENT_KEYS = {
    "pca": "filter_attempt_id",
    "cluster": "pca_attempt_id",
    "marker": "cluster_attempt_id",
}


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

    def stage_attempts(self, stage: str) -> list[dict[str, Any]]:
        """返回阶段 attempt 列表副本；未知阶段抛 ValueError。"""
        key = _STAGE_KEYS.get(stage)
        if key is None:
            raise ValueError(f"未知阶段: {stage}")
        return list(self._raw.get(key) or [])

    def attempt(
        self, stage: str, attempt_id: str, parent_id: str | None = None
    ) -> dict[str, Any] | None:
        """按 attempt_id 返回首个匹配；给定父 ID 时同时匹配该阶段的父列。"""
        if stage not in _STAGE_KEYS:
            raise ValueError(f"未知阶段: {stage}")
        parent_key = _STAGE_PARENT_KEYS.get(stage)
        if parent_id is not None and parent_key is None:
            return None
        return next(
            (
                item
                for item in self.stage_attempts(stage)
                if item.get("attempt_id") == attempt_id
                and (parent_id is None or item.get(parent_key) == parent_id)
            ),
            None,
        )

    def stage_dirs(self) -> list[tuple[str, str]]:
        """返回 QC 与四阶段的非空产物目录，顺序与 run_dirs 一致。"""
        pairs = []
        if self._raw.get("directory"):
            pairs.append(("qc", str(self._raw["directory"])))
        for stage in _STAGE_KEYS:
            for attempt in self.stage_attempts(stage):
                if attempt.get("directory"):
                    pairs.append((stage, str(attempt["directory"])))
        return pairs

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
