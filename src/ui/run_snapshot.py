"""QC run 快照：九表聚合结果与阶段访问器。"""

from __future__ import annotations

import json
import sqlite3
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

    @classmethod
    def load(cls, conn: sqlite3.Connection, session_id: str, run_id: str) -> RunSnapshot | None:
        """从单个连接读取权威 run 与预览/批准历史，并完成全部 JSON/布尔解码。"""
        conn.execute("BEGIN")
        row = conn.execute(
            "SELECT * FROM qc_runs WHERE session_id = ? AND run_id = ?", (session_id, run_id)
        ).fetchone()
        if row is None:
            return None
        previews = conn.execute(
            "SELECT * FROM qc_previews WHERE run_id = ? ORDER BY rowid", (run_id,)
        ).fetchall()
        attempts = conn.execute(
            "SELECT * FROM sc_filter_attempts WHERE run_id = ? ORDER BY rowid", (run_id,)
        ).fetchall()
        pca_attempts = conn.execute(
            "SELECT * FROM sc_pca_attempts WHERE run_id = ? ORDER BY rowid", (run_id,)
        ).fetchall()
        dimension_reviews = conn.execute(
            "SELECT d.* FROM sc_pca_dimension_reviews d JOIN sc_pca_attempts a "
            "ON d.pca_attempt_id = a.attempt_id WHERE a.run_id = ? ORDER BY d.rowid",
            (run_id,),
        ).fetchall()
        cluster_attempts = conn.execute(
            "SELECT * FROM sc_cluster_attempts WHERE run_id = ? ORDER BY rowid", (run_id,)
        ).fetchall()
        cluster_reviews = conn.execute(
            "SELECT d.* FROM sc_cluster_reviews d JOIN sc_cluster_attempts a "
            "ON d.cluster_attempt_id = a.attempt_id WHERE a.run_id = ? ORDER BY d.rowid",
            (run_id,),
        ).fetchall()
        marker_attempts = conn.execute(
            "SELECT * FROM sc_marker_attempts WHERE run_id = ? ORDER BY rowid", (run_id,)
        ).fetchall()
        cluster_rejections = conn.execute(
            "SELECT d.* FROM sc_cluster_rejections d JOIN sc_cluster_attempts a "
            "ON d.cluster_attempt_id = a.attempt_id WHERE a.run_id = ? ORDER BY d.rowid",
            (run_id,),
        ).fetchall()
        marker_reviews = conn.execute(
            "SELECT d.* FROM sc_marker_reviews d JOIN sc_marker_attempts a "
            "ON d.marker_attempt_id = a.attempt_id WHERE a.run_id = ? ORDER BY d.rowid",
            (run_id,),
        ).fetchall()
        run = dict(row)
        run["evidence"] = json.loads(run["evidence"])
        run["previews"] = [
            {
                **json.loads(p["payload"]),
                "status": p["status"],
                "approved_at": p["approved_at"],
                "rejected_at": p["rejected_at"],
                "reviewer": p["reviewer"],
                "reason": p["reason"],
                "message_id": p["message_id"],
            }
            for p in previews
        ]
        run["attempts"] = [
            {**dict(a), "result": json.loads(a["result"]) if a["result"] else None}
            for a in attempts
        ]
        run["pca_attempts"] = [
            {**dict(a), "result": json.loads(a["result"]) if a["result"] else None}
            for a in pca_attempts
        ]
        run["dimension_reviews"] = [
            {**dict(d), "dims": json.loads(d["dims"])} for d in dimension_reviews
        ]
        run["cluster_attempts"] = [
            {
                **dict(a),
                "snapshot": json.loads(a["snapshot"]),
                "result": json.loads(a["result"]) if a["result"] else None,
            }
            for a in cluster_attempts
        ]
        run["cluster_reviews"] = [
            {**dict(d), "accepted": bool(d["accepted"])} for d in cluster_reviews
        ]
        run["marker_attempts"] = [
            {
                **dict(a),
                "snapshot": json.loads(a["snapshot"]),
                "result": json.loads(a["result"]) if a["result"] else None,
            }
            for a in marker_attempts
        ]
        run["cluster_rejections"] = [dict(d) for d in cluster_rejections]
        run["marker_reviews"] = [dict(d) for d in marker_reviews]
        return cls(run)

    @classmethod
    def coerce(cls, value: Mapping[str, Any]) -> RunSnapshot:
        """普通 Mapping 包装为快照；已是快照时原样返回。"""
        return value if isinstance(value, cls) else cls(dict(value))

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

    def running_stages(self) -> set[str]:
        """由阶段访问器投影运行阶段，供复核事实与路由门控共同消费。"""
        return {
            stage
            for stage in _STAGE_KEYS
            if any(attempt["status"] == "running" for attempt in self.stage_attempts(stage))
        }

    def attempt(
        self, stage: str, attempt_id: str, parent_id: str | None = None
    ) -> dict[str, Any] | None:
        """按 attempt_id 返回首个匹配；不支持父 ID 的父阶段抛 ValueError，缺父列抛 KeyError。"""
        if stage not in _STAGE_KEYS:
            raise ValueError(f"未知阶段: {stage}")
        parent_key = _STAGE_PARENT_KEYS.get(stage)
        if parent_id is not None and parent_key is None:
            raise ValueError(f"阶段 {stage} 不接受父 ID")
        return next(
            (
                item
                for item in self.stage_attempts(stage)
                if item.get("attempt_id") == attempt_id
                and (parent_id is None or item[parent_key] == parent_id)
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
