"""Phase 2 端到端：workflow → WRROC → Snapshot → Replay"""
import tempfile
from pathlib import Path

from src.main import CellSpatioAgent


def test_full_workflow_with_snapshot(monkeypatch):
    """完整链路：配置 Agent → 执行分析 → 自动生成 WRROC + Snapshot → Replay 复现"""
    monkeypatch.setattr("src.main.get_current_llm", lambda: (None, "test-model"))
    monkeypatch.setattr("src.main.LightRAGClient.__init__", lambda self, *a, **k: None)

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        import subprocess
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)

        csv_file = repo / "counts.csv"
        csv_file.write_text("gene,sample1,sample2\nTP53,10,12\nGAPDH,100,110\n", encoding="utf-8")

        config = {"knowledge_dir": str(repo / "kb"), "data_dir": str(repo / "data")}
        agent = CellSpatioAgent(config)

        # 执行分析（无 LLM → 回退旧路径）
        context = {"downloaded_assets": [{"source": "geo", "asset_id": "GSE1", "access_path": str(csv_file)}]}
        result = agent.execute_workflow("分析差异表达基因", context=context)

        # 应进入 HITL 确认门
        assert result["status"] in ("needs_script_confirmation", "success")
        assert result["analysis_type"] == "differential_expression"

        # 验证 WRROC 生成
        from src.control.wrroc_store import WRROCStore
        store = WRROCStore(base_dir=repo / ".wrroc")
        runs = store.list_runs()
        assert len(runs) >= 1
        run = runs[-1]
        assert run.intent.analysis_type == "differential_expression"
        assert len(run.steps) >= 1
        assert run.steps[0].tool == "run_analysis"

        # 验证 Snapshot 生成
        from src.control.snapshot_manager import SnapshotManager
        mgr = SnapshotManager(repo_root=repo)
        snaps = mgr.list_snapshots()
        assert len(snaps) >= 1
        last_snap_id = list(snaps.keys())[-1]
        assert mgr.get_snapshot_path(last_snap_id).exists()

        # Replay 复现
        from src.control.replay import replay_run
        replay_result = replay_run(last_snap_id, repo_root=repo)
        assert replay_result["status"] == "success"