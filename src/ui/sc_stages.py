"""受控阶段操作的窄接口；SessionStore 只暴露 ``stages`` 这一组方法。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.ui.sc_stage_defs import STAGE_SPECS
from src.ui.session_codec import _encode_results, _now
from src.ui.stage_lifecycle import StageLifecycle

if TYPE_CHECKING:
    from src.ui.session_store import SessionStore


class ScStages:
    """阶段领取、完成、人工记录与启动中断的 facade。"""

    def __init__(self, store: SessionStore) -> None:
        """绑定拥有连接工厂的会话存储。"""
        self._store = store

    def claim_filter(
        self,
        session_id: str,
        run_id: str,
        revision: int,
        preview_id: str,
        binding: str,
        tool_version: str,
        directory: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        """认领过滤阶段；同绑定只执行一次。"""
        return self._store._claim_filter(
            session_id,
            run_id,
            revision,
            preview_id,
            binding,
            tool_version,
            directory,
            attempt_id,
        )

    def finish_filter(self, attempt_id: str, result: dict[str, Any]) -> None:
        """落库过滤终态与阶段消息。"""
        self._store._finish_filter(attempt_id, result)

    def claim_pca(
        self,
        session_id: str,
        run_id: str,
        revision: int,
        filter_attempt_id: str,
        binding: str,
        tool_version: str,
        protocol: dict[str, Any],
        reason: str,
        directory: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        """认领 PCA 阶段。"""
        return self._store._claim_pca(
            session_id,
            run_id,
            revision,
            filter_attempt_id,
            binding,
            tool_version,
            protocol,
            reason,
            directory,
            attempt_id,
        )

    def finish_pca(self, attempt_id: str, result: dict[str, Any]) -> None:
        """落库 PCA 终态与阶段消息。"""
        self._store._finish_pca(attempt_id, result)

    def record_dimensions(
        self,
        session_id: str,
        run: dict[str, Any],
        parent: dict[str, Any],
        attempt: dict[str, Any],
        binding: str,
        version: str,
        revision: int,
        dims: list[int],
        reason: str,
    ) -> None:
        """记录人工 PCA 维度选择。"""
        self._store._record_dimensions(
            session_id, run, parent, attempt, binding, version, revision, dims, reason
        )

    def claim_cluster(
        self,
        session_id: str,
        run_id: str,
        revision: int,
        snapshot: dict[str, Any],
        binding: str,
        tool_version: str,
        protocol: dict[str, Any],
        reason: str,
        directory: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        """认领聚类阶段。"""
        return self._store._claim_cluster(
            session_id,
            run_id,
            revision,
            snapshot,
            binding,
            tool_version,
            protocol,
            reason,
            directory,
            attempt_id,
        )

    def finish_cluster(
        self,
        attempt_id: str,
        result: dict[str, Any],
        expected: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """落库聚类终态并返回数据库权威结果。"""
        return self._store._finish_cluster(attempt_id, result, expected)

    def record_cluster_review(
        self,
        session_id: str,
        run: dict[str, Any],
        cluster: dict[str, Any],
        binding: str,
        version: str,
        revision: int,
        reason: str,
    ) -> None:
        """记录聚类人工通过/要求补充。"""
        self._store._record_cluster_review(
            session_id, run, cluster, binding, version, revision, reason
        )

    def record_cluster_rejection(
        self,
        session_id: str,
        run: dict[str, Any],
        cluster: dict[str, Any],
        binding: str,
        version: str,
        revision: int,
        reason: str,
        reviewer: str = "local-user",
    ) -> None:
        """记录聚类人工拒绝。"""
        self._store._record_cluster_rejection(
            session_id, run, cluster, binding, version, revision, reason, reviewer
        )

    def claim_marker(
        self,
        session_id: str,
        run_id: str,
        revision: int,
        snapshot: dict[str, Any],
        binding: str,
        tool_version: str,
        protocol: dict[str, Any],
        reason: str,
        directory: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        """认领 marker 阶段。"""
        return self._store._claim_marker(
            session_id,
            run_id,
            revision,
            snapshot,
            binding,
            tool_version,
            protocol,
            reason,
            directory,
            attempt_id,
        )

    def finish_marker(
        self,
        attempt_id: str,
        result: dict[str, Any],
        expected: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """落库 marker 终态并返回数据库权威结果。"""
        return self._store._finish_marker(attempt_id, result, expected)

    def record_marker_review(
        self,
        session_id: str,
        marker_attempt_id: str,
        decision: str,
        reason: str,
        reviewer: str,
        version: str,
        *,
        revision: int,
    ) -> dict[str, Any]:
        """记录 marker 人工复核。"""
        return self._store._record_marker_review(
            session_id, marker_attempt_id, decision, reason, reviewer, version, revision=revision
        )

    def interrupt_all(self) -> None:
        """启动恢复：中断全部遗留 running，不接管、不自动重跑。"""
        lifecycle = StageLifecycle(self._store._conn, _encode_results)
        for spec in STAGE_SPECS:
            if spec.interrupt_result is None:
                with self._store._conn() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute(
                        f"UPDATE {spec.table} SET status = 'interrupted', finished_at = ? "
                        "WHERE status = 'running'",
                        (_now(),),
                    )
                continue
            with self._store._conn() as conn:
                ids = [
                    row[0]
                    for row in conn.execute(
                        f"SELECT attempt_id FROM {spec.table} WHERE status = 'running'"
                    )
                ]
            for attempt_id in ids:
                lifecycle.finish(
                    spec,
                    attempt_id=attempt_id,
                    result=spec.interrupt_result,
                    clock=_now,
                )
