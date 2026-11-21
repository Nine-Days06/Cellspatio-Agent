"""受控阶段操作的窄接口；SessionStore 只暴露 ``stages`` 这一组方法。"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any

from src.ui.run_snapshot import RunSnapshot
from src.ui.sc_stage_defs import (
    CLUSTER_STAGE,
    FILTER_STAGE,
    MARKER_STAGE,
    PCA_STAGE,
    STAGE_SPECS,
)
from src.ui.session_codec import _encode_results, _now
from src.ui.stage_lifecycle import (
    ReviewFactPlan,
    StageLifecycle,
    advance_run,
    append_review_fact,
    publish_message,
)

if TYPE_CHECKING:
    from src.ui.session_store import SessionStore


class ScStages:
    """阶段领取、完成、人工记录与启动中断的 facade。"""

    def __init__(self, store: SessionStore) -> None:
        """绑定拥有连接工厂的会话存储。"""
        self._store = store

    @property
    def _conn(self) -> Callable[[], AbstractContextManager[sqlite3.Connection]]:
        """复用 SessionStore 的短连接工厂。"""
        return self._store._conn

    def _require_no_filter(self, conn: sqlite3.Connection, run_id: str) -> None:
        """复用 SessionStore 的运行中阶段检查。"""
        self._store._require_no_filter(conn, run_id)

    def get_qc_run(self, session_id: str, run_id: str) -> RunSnapshot | None:
        """读取 QC 及全部阶段历史。"""
        return self._store.get_qc_run(session_id, run_id)

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
        """短事务认领当前已批准选择；相同绑定只执行一次。"""

        def validate(conn: sqlite3.Connection, run: sqlite3.Row) -> None:
            """锁内确认没有阶段在跑且预览仍是已批准的精确选择。"""
            self._require_no_filter(conn, run_id)
            preview = conn.execute(
                "SELECT * FROM qc_previews WHERE run_id = ? AND preview_id = ?",
                (run_id, preview_id),
            ).fetchone()
            if (
                run["active_preview_id"] != preview_id
                or preview is None
                or preview["status"] != "approved"
                or json.loads(preview["payload"])["binding_sha256"] != binding
            ):
                raise ValueError("仅可执行当前已批准的精确选择，请刷新")

        return StageLifecycle(self._conn, _encode_results).claim(
            FILTER_STAGE,
            session_id=session_id,
            run_id=run_id,
            revision=revision,
            attempt_id=attempt_id,
            values={
                "preview_id": preview_id,
                "binding_sha256": binding,
                "tool_version": tool_version,
                "directory": directory,
            },
            validate=validate,
            clock=_now,
        )

    def finish_filter(self, attempt_id: str, result: dict[str, Any]) -> None:
        """技术终态与单条阶段消息原子落库，不暴露每细胞证据。"""
        StageLifecycle(self._conn, _encode_results).finish(
            FILTER_STAGE, attempt_id=attempt_id, result=result, clock=_now
        )

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
        """短事务认领独立授权；哈希期间的选择变化通过 revision 拒绝。"""

        def validate(conn: sqlite3.Connection, run: sqlite3.Row) -> None:
            """锁内确认父过滤已完成且仍是当前批准选择。"""
            self._require_no_filter(conn, run_id)
            parent = conn.execute(
                "SELECT a.*, p.status AS approval FROM sc_filter_attempts a "
                "JOIN qc_previews p USING(preview_id) WHERE a.attempt_id = ? AND a.run_id = ?",
                (filter_attempt_id, run_id),
            ).fetchone()
            if (
                parent is None
                or parent["status"] != "completed"
                or parent["approval"] != "approved"
                or run["active_preview_id"] != parent["preview_id"]
            ):
                raise ValueError("仅可授权当前已批准且过滤完成的父阶段，请刷新")

        return StageLifecycle(self._conn, _encode_results).claim(
            PCA_STAGE,
            session_id=session_id,
            run_id=run_id,
            revision=revision,
            attempt_id=attempt_id,
            values={
                "filter_attempt_id": filter_attempt_id,
                "binding_sha256": binding,
                "tool_version": tool_version,
                "protocol": json.dumps(protocol, ensure_ascii=False),
                "reason": reason,
                "directory": directory,
            },
            validate=validate,
            clock=_now,
        )

    def finish_pca(self, attempt_id: str, result: dict[str, Any]) -> None:
        """终态和安全阶段消息原子保存；私有分析证据不进入聊天。"""
        StageLifecycle(self._conn, _encode_results).finish(
            PCA_STAGE, attempt_id=attempt_id, result=result, clock=_now
        )

    def record_dimensions(
        self,
        session_id: str,
        run: Mapping[str, Any],
        parent: dict[str, Any],
        attempt: dict[str, Any],
        binding: str,
        version: str,
        revision: int,
        dims: list[int],
        reason: str,
    ) -> None:
        """追加人工事实与安全消息；CAS 防止校验期间发生选择变化。"""

        # shortcut: 文件哈希在事务外检查，本地证据可被外部改写；生产隔离另片处理。
        def run_row(conn: sqlite3.Connection) -> sqlite3.Row | None:
            """按会话读取当前 run。"""
            return conn.execute(
                "SELECT * FROM qc_runs WHERE session_id = ? AND run_id = ?",
                (session_id, run["run_id"]),
            ).fetchone()

        def require_run(conn: sqlite3.Connection) -> None:
            """previous 命中前仍确认 QC 关联存在。"""
            if run_row(conn) is None:
                raise ValueError("QC 关联不存在")

        def validate(conn: sqlite3.Connection) -> None:
            """锁内重查 run、父选择与 PCA 身份。"""
            row = run_row(conn)
            if row is None:
                raise ValueError("QC 关联不存在")
            self._require_no_filter(conn, run["run_id"])
            live_parent = conn.execute(
                "SELECT a.*, p.status AS approval FROM sc_filter_attempts a "
                "JOIN qc_previews p USING(preview_id) WHERE a.attempt_id = ? AND a.run_id = ?",
                (parent["attempt_id"], run["run_id"]),
            ).fetchone()
            live_pca = conn.execute(
                "SELECT * FROM sc_pca_attempts WHERE attempt_id = ? AND run_id = ?",
                (attempt["attempt_id"], run["run_id"]),
            ).fetchone()
            if (
                row["revision"] != revision
                or json.loads(row["evidence"]) != run["evidence"]
                or live_parent is None
                or live_parent["status"] != "completed"
                or live_parent["approval"] != "approved"
                or row["active_preview_id"] != parent["preview_id"]
                or any(
                    live_parent[k] != parent[k]
                    for k in ("binding_sha256", "tool_version", "directory")
                )
                or json.loads(live_parent["result"]) != parent["result"]
                or live_pca is None
                or live_pca["status"] != "completed"
                or any(
                    live_pca[k] != attempt[k]
                    for k in (
                        "filter_attempt_id",
                        "binding_sha256",
                        "tool_version",
                        "protocol",
                        "directory",
                    )
                )
                or json.loads(live_pca["result"]) != attempt["result"]
            ):
                raise ValueError("PCA 或父选择已变化，请刷新后重新记录")

        def same_previous(previous: sqlite3.Row) -> bool:
            """同 revision 的维度与依据必须一致。"""
            return json.loads(previous["dims"]) == dims and previous["reason"] == reason

        def same_latest(latest: sqlite3.Row) -> bool:
            """latest+1 的绑定、版本、维度与依据一致时抑制重复。"""
            return (
                latest["binding_sha256"] == binding
                and latest["record_version"] == version
                and json.loads(latest["dims"]) == dims
                and latest["reason"] == reason
            )

        def message(fact: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            """维度消息只暴露聚合标识。"""
            return (
                "维度选择已记录，科学未复核；未授权下游执行。",
                {
                    "pca_dimensions": {
                        "run_id": run["run_id"],
                        "filter_attempt_id": parent["attempt_id"],
                        "attempt_id": attempt["attempt_id"],
                        "review_id": fact["review_id"],
                        "stage": "pca_review",
                        "scientific_status": "unreviewed",
                    }
                },
            )

        identity = {
            "pca_attempt_id": attempt["attempt_id"],
            "binding_sha256": binding,
            "record_version": version,
            "source_revision": revision,
        }
        append_review_fact(
            self._conn,
            _encode_results,
            ReviewFactPlan(
                table="sc_pca_dimension_reviews",
                previous_match=identity,
                insert_values={
                    **identity,
                    "dims": json.dumps(dims),
                    "reason": reason,
                },
                same_previous=same_previous,
                conflict_message="同一 revision 的记录内容冲突，请刷新",
                latest_match={"pca_attempt_id": attempt["attempt_id"]},
                same_latest=same_latest,
                message=message,
                run_id=run["run_id"],
                session_id=session_id,
            ),
            before_previous=require_run,
            validate=validate,
            clock=_now,
        )

    @staticmethod
    def _cluster_source_matches(
        conn: sqlite3.Connection, run: sqlite3.Row, snapshot: dict[str, Any]
    ) -> bool:
        """只比较认领快照，不把本次 revision 推进当上游失效。"""
        if json.loads(run["evidence"]) != snapshot["evidence"]:
            return False
        parent, pca, review = snapshot["parent"], snapshot["pca"], snapshot["review"]
        live_parent = conn.execute(
            "SELECT a.*, p.status AS approval FROM sc_filter_attempts a JOIN qc_previews p USING(preview_id) "
            "WHERE a.attempt_id = ? AND a.run_id = ?",
            (parent["attempt_id"], run["run_id"]),
        ).fetchone()
        live_pca = conn.execute(
            "SELECT * FROM sc_pca_attempts WHERE attempt_id = ? AND run_id = ?",
            (pca["attempt_id"], run["run_id"]),
        ).fetchone()
        live_review = conn.execute(
            "SELECT * FROM sc_pca_dimension_reviews WHERE pca_attempt_id = ? ORDER BY rowid DESC LIMIT 1",
            (pca["attempt_id"],),
        ).fetchone()
        return bool(
            live_parent
            and live_pca
            and live_review
            and run["active_preview_id"] == parent["preview_id"]
            and live_parent["approval"] == "approved"
            and live_parent["status"] == live_pca["status"] == "completed"
            and all(
                live_parent[k] == parent[k]
                for k in ("preview_id", "binding_sha256", "tool_version", "directory")
            )
            and json.loads(live_parent["result"]) == parent["result"]
            and all(
                live_pca[k] == pca[k]
                for k in (
                    "filter_attempt_id",
                    "binding_sha256",
                    "tool_version",
                    "protocol",
                    "directory",
                )
            )
            and json.loads(live_pca["result"]) == pca["result"]
            and {**dict(live_review), "dims": json.loads(live_review["dims"])} == review
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
        """短事务 CAS 认领，保存完整授权维度和父身份快照。"""

        def validate(conn: sqlite3.Connection, run: sqlite3.Row) -> None:
            """锁内确认维度记录与父身份仍与认领快照一致。"""
            self._require_no_filter(conn, run_id)
            if snapshot["review"]["source_revision"] + 1 != revision or not (
                self._cluster_source_matches(conn, run, snapshot)
            ):
                raise ValueError("维度或父身份已变化，请刷新后重新授权")

        def check_previous(previous: sqlite3.Row) -> None:
            """同一来源必须对应相同维度记录。"""
            if previous["review_id"] != snapshot["review"]["review_id"]:
                raise ValueError("维度关联冲突")

        def claimed_row(conn: sqlite3.Connection, attempt_id: str) -> dict[str, Any]:
            """在领取事务内读回完整 attempt，供完成时比对权威快照。"""
            row = dict(
                conn.execute(
                    "SELECT * FROM sc_cluster_attempts WHERE attempt_id = ?", (attempt_id,)
                ).fetchone()
            )
            return {**row, "snapshot": snapshot}

        claimed = StageLifecycle(self._conn, _encode_results).claim(
            CLUSTER_STAGE,
            session_id=session_id,
            run_id=run_id,
            revision=revision,
            attempt_id=attempt_id,
            values={
                "pca_attempt_id": snapshot["pca"]["attempt_id"],
                "review_id": snapshot["review"]["review_id"],
                "snapshot": json.dumps(snapshot, ensure_ascii=False),
                "binding_sha256": binding,
                "tool_version": tool_version,
                "protocol": json.dumps(protocol),
                "source_revision": revision,
                "claim_revision": revision + 1,
                "reason": reason,
                "directory": directory,
            },
            validate=validate,
            check_previous=check_previous,
            after_claim=claimed_row,
            clock=_now,
        )
        return claimed

    def finish_cluster(
        self,
        attempt_id: str,
        result: dict[str, Any],
        expected: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """终态/消息/revision 原子保存；外部改变只阻止成功，不阻止失败收口。"""

        def finalize(
            conn: sqlite3.Connection, attempt: sqlite3.Row, result: dict[str, Any]
        ) -> dict[str, Any]:
            """完成前复核授权快照；一旦失效只降级成功，不阻止失败收口。"""
            if result["status"] != "completed":
                return result
            run = conn.execute(
                "SELECT * FROM qc_runs WHERE run_id = ?", (attempt["run_id"],)
            ).fetchone()
            snapshot = json.loads(attempt["snapshot"])
            if (
                expected is None
                or run["revision"] != expected["claim_revision"]
                or snapshot != expected["snapshot"]
                or any(
                    attempt[k] != expected[k]
                    for k in (
                        "run_id",
                        "pca_attempt_id",
                        "review_id",
                        "binding_sha256",
                        "tool_version",
                        "protocol",
                        "source_revision",
                        "claim_revision",
                        "directory",
                        "reason",
                    )
                )
                or not self._cluster_source_matches(conn, run, snapshot)
            ):
                return {**result, "status": "failed", "error": "运行中授权快照或父身份发生变化"}
            return result

        return StageLifecycle(self._conn, _encode_results).finish(
            CLUSTER_STAGE, attempt_id=attempt_id, result=result, finalize=finalize, clock=_now
        )

    @classmethod
    def _completed_cluster_matches(
        cls, conn: sqlite3.Connection, run: sqlite3.Row, cluster: dict[str, Any]
    ) -> bool:
        """短事务重查完整聚类身份及其父快照；文件仍在事务外验证。"""
        row = conn.execute(
            "SELECT * FROM sc_cluster_attempts WHERE attempt_id = ? AND run_id = ?",
            (cluster["attempt_id"], run["run_id"]),
        ).fetchone()
        if row is None or row["status"] != "completed":
            return False
        live = {
            **dict(row),
            "snapshot": json.loads(row["snapshot"]),
            "result": json.loads(row["result"]),
        }
        return live == cluster and cls._cluster_source_matches(conn, run, cluster["snapshot"])

    def record_cluster_review(
        self,
        session_id: str,
        run: Mapping[str, Any],
        cluster: dict[str, Any],
        binding: str,
        version: str,
        revision: int,
        reason: str,
    ) -> None:
        """接受事实、安全消息与 revision 原子追加；不授权或启动 marker。"""

        # shortcut: 哈希在事务外核实；本地文件不可变隔离留给生产部署片。
        def run_row(conn: sqlite3.Connection) -> sqlite3.Row | None:
            """按会话读取当前 run。"""
            return conn.execute(
                "SELECT * FROM qc_runs WHERE session_id = ? AND run_id = ?",
                (session_id, run["run_id"]),
            ).fetchone()

        def require_run(conn: sqlite3.Connection) -> None:
            """previous 命中前仍确认 QC 关联存在。"""
            if run_row(conn) is None:
                raise ValueError("QC 关联不存在")

        def validate(conn: sqlite3.Connection) -> None:
            """锁内重查 run、父快照与已完成聚类。"""
            row = run_row(conn)
            if row is None:
                raise ValueError("QC 关联不存在")
            self._require_no_filter(conn, run["run_id"])
            if row["revision"] != revision or not self._completed_cluster_matches(
                conn, row, cluster
            ):
                raise ValueError("聚类或父身份已变化，请刷新后重新记录")

        def same_previous(previous: sqlite3.Row) -> bool:
            """同 revision 的接受标记与依据必须一致。"""
            return previous["accepted"] == 1 and previous["reason"] == reason

        def same_latest(latest: sqlite3.Row) -> bool:
            """latest+1 的绑定、版本与依据一致时抑制重复。"""
            return (
                latest["binding_sha256"] == binding
                and latest["record_version"] == version
                and latest["reason"] == reason
            )

        def message(fact: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            """接受消息只暴露聚合标识。"""
            return (
                "探索性聚类接受已记录，科学未复核；未授权 marker 或注释。",
                {
                    "sc_cluster_review": {
                        "run_id": run["run_id"],
                        "cluster_attempt_id": cluster["attempt_id"],
                        "review_id": fact["review_id"],
                        "stage": "cluster_review",
                        "scientific_status": "unreviewed",
                    }
                },
            )

        identity = {
            "cluster_attempt_id": cluster["attempt_id"],
            "binding_sha256": binding,
            "record_version": version,
            "source_revision": revision,
        }
        append_review_fact(
            self._conn,
            _encode_results,
            ReviewFactPlan(
                table="sc_cluster_reviews",
                previous_match=identity,
                insert_values={
                    **identity,
                    "accepted": 1,
                    "reason": reason,
                },
                same_previous=same_previous,
                conflict_message="同一 revision 的聚类复核内容冲突",
                latest_match={"cluster_attempt_id": cluster["attempt_id"]},
                same_latest=same_latest,
                message=message,
                run_id=run["run_id"],
                session_id=session_id,
            ),
            before_previous=require_run,
            validate=validate,
            clock=_now,
        )

    def record_cluster_rejection(
        self,
        session_id: str,
        run: Mapping[str, Any],
        cluster: dict[str, Any],
        binding: str,
        version: str,
        revision: int,
        reason: str,
        reviewer: str = "local-user",
    ) -> None:
        """追加聚类拒绝事实；不改写既有接受历史，不授权 marker。"""
        reason = reason.strip()
        if not reason:
            raise ValueError("拒绝必须填写原因")
        reviewer = reviewer.strip() or "local-user"

        def validate(conn: sqlite3.Connection) -> None:
            """锁内重查 run、聚类完成态与 revision。"""
            row = conn.execute(
                "SELECT * FROM qc_runs WHERE session_id = ? AND run_id = ?",
                (session_id, run["run_id"]),
            ).fetchone()
            attempt = conn.execute(
                "SELECT * FROM sc_cluster_attempts WHERE attempt_id = ? AND run_id = ?",
                (cluster["attempt_id"], run["run_id"]),
            ).fetchone()
            if row is None or attempt is None:
                raise ValueError("聚类关联不存在")
            if attempt["status"] != "completed" or attempt["binding_sha256"] != binding:
                raise ValueError("聚类未完成或绑定不符")
            if row["revision"] != revision:
                raise ValueError("聚类记录已失效，请刷新")

        def before_insert(conn: sqlite3.Connection) -> None:
            """同 revision 已有接受时拒绝；运行中阶段禁止记录。"""
            if conn.execute(
                "SELECT 1 FROM sc_cluster_reviews WHERE cluster_attempt_id = ? "
                "AND binding_sha256 = ? AND record_version = ? AND source_revision = ?",
                (cluster["attempt_id"], binding, version, revision),
            ).fetchone():
                raise ValueError("同一 revision 已有聚类接受事实")
            self._require_no_filter(conn, run["run_id"])

        def same_previous(previous: sqlite3.Row) -> bool:
            """同 revision 的原因与复核人必须一致。"""
            return previous["reason"] == reason and previous["reviewer"] == reviewer

        def same_latest(latest: sqlite3.Row) -> bool:
            """latest+1 的绑定、版本、原因与复核人一致时抑制重复。"""
            return (
                latest["binding_sha256"] == binding
                and latest["record_version"] == version
                and latest["reason"] == reason
                and latest["reviewer"] == reviewer
            )

        def message(fact: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            """拒绝消息只暴露聚合标识。"""
            return (
                "探索性聚类拒绝已记录；未授权 marker 或注释。",
                {
                    "sc_cluster_rejection": {
                        "run_id": run["run_id"],
                        "cluster_attempt_id": cluster["attempt_id"],
                        "stage": "cluster_review",
                        "scientific_status": "unreviewed",
                    }
                },
            )

        identity = {
            "cluster_attempt_id": cluster["attempt_id"],
            "binding_sha256": binding,
            "record_version": version,
            "source_revision": revision,
        }
        append_review_fact(
            self._conn,
            _encode_results,
            ReviewFactPlan(
                table="sc_cluster_rejections",
                previous_match=identity,
                insert_values={
                    **identity,
                    "reason": reason,
                    "reviewer": reviewer,
                },
                same_previous=same_previous,
                conflict_message="同一 revision 的聚类拒绝内容冲突",
                latest_match={"cluster_attempt_id": cluster["attempt_id"]},
                same_latest=same_latest,
                message=message,
                run_id=run["run_id"],
                session_id=session_id,
            ),
            validate=validate,
            before_insert=before_insert,
            validate_first=True,
            clock=_now,
        )

    @classmethod
    def _marker_source_matches(
        cls, conn: sqlite3.Connection, run: sqlite3.Row, snapshot: dict[str, Any]
    ) -> bool:
        """完整当前接受记录与聚类身份必须和认领快照相等。"""
        cluster, review = snapshot["cluster"], snapshot["review"]
        row = conn.execute(
            "SELECT * FROM sc_cluster_reviews WHERE cluster_attempt_id = ? ORDER BY rowid DESC LIMIT 1",
            (cluster["attempt_id"],),
        ).fetchone()
        return bool(
            row
            and {**dict(row), "accepted": bool(row["accepted"])} == review
            and cls._completed_cluster_matches(conn, run, cluster)
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
        """短事务 CAS 认领，保存完整聚类接受和父身份快照。"""

        def validate(conn: sqlite3.Connection, run: sqlite3.Row) -> None:
            """锁内确认聚类接受记录与父身份仍与认领快照一致。"""
            self._require_no_filter(conn, run_id)
            if snapshot["review"]["source_revision"] + 1 != revision or not (
                self._marker_source_matches(conn, run, snapshot)
            ):
                raise ValueError("聚类接受或父身份已变化，请刷新后重新授权")

        def check_previous(previous: sqlite3.Row) -> None:
            """同一来源必须对应相同聚类复核记录。"""
            if previous["review_id"] != snapshot["review"]["review_id"]:
                raise ValueError("聚类复核关联冲突")

        def claimed_row(conn: sqlite3.Connection, attempt_id: str) -> dict[str, Any]:
            """在领取事务内读回完整 attempt，供完成时比对权威快照。"""
            row = dict(
                conn.execute(
                    "SELECT * FROM sc_marker_attempts WHERE attempt_id = ?", (attempt_id,)
                ).fetchone()
            )
            return {**row, "snapshot": snapshot}

        return StageLifecycle(self._conn, _encode_results).claim(
            MARKER_STAGE,
            session_id=session_id,
            run_id=run_id,
            revision=revision,
            attempt_id=attempt_id,
            values={
                "cluster_attempt_id": snapshot["cluster"]["attempt_id"],
                "review_id": snapshot["review"]["review_id"],
                "snapshot": json.dumps(snapshot, ensure_ascii=False),
                "binding_sha256": binding,
                "tool_version": tool_version,
                "protocol": json.dumps(protocol),
                "source_revision": revision,
                "claim_revision": revision + 1,
                "reason": reason,
                "directory": directory,
            },
            validate=validate,
            check_previous=check_previous,
            after_claim=claimed_row,
            clock=_now,
        )

    def finish_marker(
        self,
        attempt_id: str,
        result: dict[str, Any],
        expected: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """终态/消息/revision 原子保存；外部改变只阻止成功，不阻止失败收口。"""

        def finalize(
            conn: sqlite3.Connection, attempt: sqlite3.Row, result: dict[str, Any]
        ) -> dict[str, Any]:
            """完成前复核授权快照；一旦失效只降级成功，不阻止失败收口。"""
            if result["status"] != "completed":
                return result
            run = conn.execute(
                "SELECT * FROM qc_runs WHERE run_id = ?", (attempt["run_id"],)
            ).fetchone()
            snapshot = json.loads(attempt["snapshot"])
            if (
                expected is None
                or run["revision"] != expected["claim_revision"]
                or snapshot != expected["snapshot"]
                or any(
                    attempt[k] != expected[k]
                    for k in (
                        "run_id",
                        "cluster_attempt_id",
                        "review_id",
                        "binding_sha256",
                        "tool_version",
                        "protocol",
                        "source_revision",
                        "claim_revision",
                        "directory",
                        "reason",
                    )
                )
                or not self._marker_source_matches(conn, run, snapshot)
            ):
                return {**result, "status": "failed", "error": "运行中授权快照或父身份发生变化"}
            return result

        return StageLifecycle(self._conn, _encode_results).finish(
            MARKER_STAGE, attempt_id=attempt_id, result=result, finalize=finalize, clock=_now
        )

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
    ) -> RunSnapshot | None:
        """追加 marker 人工复核事实；同 revision 幂等，内容冲突拒绝。"""
        if decision not in {"accepted", "rejected"}:
            raise ValueError("marker 复核结论必须是 accepted 或 rejected")
        reason = reason.strip()
        if not reason:
            raise ValueError("复核必须填写原因")
        reviewer = reviewer.strip() or "local-user"
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            attempt = conn.execute(
                "SELECT a.*, r.session_id, r.revision FROM sc_marker_attempts a "
                "JOIN qc_runs r USING(run_id) WHERE a.attempt_id = ?",
                (marker_attempt_id,),
            ).fetchone()
            if attempt is None or attempt["session_id"] != session_id:
                raise ValueError("marker 关联不存在")
            if attempt["status"] != "completed":
                raise ValueError("marker 尚未完成，不能复核")
            if attempt["revision"] != revision:
                raise ValueError("marker 记录已失效，请刷新")
            binding = attempt["binding_sha256"]
            previous = conn.execute(
                "SELECT * FROM sc_marker_reviews WHERE marker_attempt_id = ? "
                "AND binding_sha256 = ? AND record_version = ? AND source_revision = ?",
                (marker_attempt_id, binding, version, revision),
            ).fetchone()
            if previous:
                same = (
                    previous["decision"] == decision
                    and previous["reason"] == reason
                    and previous["reviewer"] == reviewer
                )
                if not same:
                    raise ValueError("同一 revision 的 marker 复核内容冲突")
                return self.get_qc_run(session_id, attempt["run_id"])
            latest = conn.execute(
                "SELECT * FROM sc_marker_reviews WHERE marker_attempt_id = ? "
                "ORDER BY rowid DESC LIMIT 1",
                (marker_attempt_id,),
            ).fetchone()
            if (
                latest
                and latest["binding_sha256"] == binding
                and latest["record_version"] == version
                and latest["source_revision"] + 1 == revision
                and latest["decision"] == decision
                and latest["reason"] == reason
                and latest["reviewer"] == reviewer
            ):
                return self.get_qc_run(session_id, attempt["run_id"])
            self._require_no_filter(conn, attempt["run_id"])
            now = _now()
            message_id = publish_message(
                conn,
                session_id,
                "marker 人工复核已记录；未执行自动注释。",
                {
                    "sc_marker_review": {
                        "run_id": attempt["run_id"],
                        "marker_attempt_id": marker_attempt_id,
                        "decision": decision,
                        "stage": "marker_review",
                        "scientific_status": "unreviewed",
                    }
                },
                now,
                _encode_results,
            )
            conn.execute(
                "INSERT INTO sc_marker_reviews "
                "(review_id, marker_attempt_id, binding_sha256, record_version, source_revision, "
                "decision, reason, reviewer, created_at, message_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    uuid.uuid4().hex,
                    marker_attempt_id,
                    binding,
                    version,
                    revision,
                    decision,
                    reason,
                    reviewer,
                    now,
                    message_id,
                ),
            )
            advance_run(conn, attempt["run_id"], session_id, now)
        return self.get_qc_run(session_id, attempt["run_id"])

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
