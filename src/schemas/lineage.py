"""Lineage 图谱模型。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class LineageNode(BaseModel):
    id: str
    type: Literal["run", "file", "step"]
    label: str
    meta: dict[str, Any] = Field(default_factory=dict)


class LineageEdge(BaseModel):
    src: str
    dst: str
    relation: Literal["input_to", "output_from", "depends_on"]


class LineageGraph(BaseModel):
    nodes: dict[str, LineageNode] = Field(default_factory=dict)
    edges: list[LineageEdge] = Field(default_factory=list)

    def add_node(self, node: LineageNode) -> None:
        self.nodes[node.id] = node

    def add_edge(self, edge: LineageEdge) -> None:
        self.edges.append(edge)

    def has_edge(self, src: str, dst: str) -> bool:
        return any(e.src == src and e.dst == dst for e in self.edges)

    def get_downstream(self, node_id: str) -> list[str]:
        return [e.dst for e in self.edges if e.src == node_id]

    def get_upstream(self, node_id: str) -> list[str]:
        return [e.src for e in self.edges if e.dst == node_id]


# 重建模型以解决前向引用问题
LineageGraph.model_rebuild()