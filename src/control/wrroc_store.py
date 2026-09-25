"""WRROC 存储：落盘、索引、查询、Lineage 图谱。"""
from __future__ import annotations

from pathlib import Path

from src.schemas.lineage import LineageEdge, LineageGraph, LineageNode
from src.schemas.workflow import WorkflowRun


class WRROCStore:
    """WRROC 记录存储：每个 run_id 对应 .wrroc/<run_id>/workflow.json"""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, Path] = {}
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        self._index = {}
        for run_dir in self.base_dir.iterdir():
            if run_dir.is_dir():
                wf_file = run_dir / "workflow.json"
                if wf_file.exists():
                    self._index[run_dir.name] = wf_file

    def persist(self, run: WorkflowRun) -> Path:
        run_dir = self.base_dir / run.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        wf_file = run_dir / "workflow.json"
        wf_file.write_text(run.to_json(), encoding="utf-8")
        self._index[run.run_id] = wf_file
        return wf_file

    def load(self, run_id: str) -> WorkflowRun | None:
        wf_file = self._index.get(run_id)
        if not wf_file or not wf_file.exists():
            return None
        return WorkflowRun.from_json(wf_file.read_text(encoding="utf-8"))

    def list_runs(self) -> list[WorkflowRun]:
        return [self.load(rid) for rid in sorted(self._index.keys())]

    def build_lineage(self, run_id: str) -> LineageGraph:
        """构建 Lineage 图谱：文件/步骤/运行节点 + 依赖边"""
        graph = LineageGraph()
        run = self.load(run_id)
        if not run:
            return graph
        self._walk_lineage(run, graph, set())
        return graph

    def _walk_lineage(self, run: WorkflowRun, graph: LineageGraph, visited: set[str]) -> None:
        if run.run_id in visited:
            return
        visited.add(run.run_id)
        # 节点：运行
        graph.add_node(LineageNode(id=f"run:{run.run_id}", type="run", label=run.intent.analysis_type or run.intent.type))
        # 节点：输入文件（从 context.downloaded_assets）
        for asset in run.input.context.get("downloaded_assets", []):
            asset_path = asset.get("access_path")
            if asset_path:
                graph.add_node(LineageNode(id=f"file:{asset_path}", type="file", label=asset_path))
                graph.add_edge(LineageEdge(src=f"file:{asset_path}", dst=f"run:{run.run_id}", relation="input_to"))
        # 节点：输出文件
        for out in run.outputs:
            graph.add_node(LineageNode(id=f"file:{out.path}", type="file", label=out.path))
            graph.add_edge(LineageEdge(src=f"run:{run.run_id}", dst=f"file:{out.path}", relation="output_from"))
            # 反向查找：哪些 run 使用了该文件作为输入
            for other_run in self.list_runs():
                if other_run.run_id != run.run_id:
                    for asset in other_run.input.context.get("downloaded_assets", []):
                        if asset.get("access_path") == out.path:
                            graph.add_edge(LineageEdge(src=f"file:{out.path}", dst=f"run:{other_run.run_id}", relation="input_to"))
        # 递归（简单版：仅直接依赖）