"""Agent tool contract: schemas, terminal statuses, system prompt.

Design rationale (2026 SOTA alignment):
- ChatSpatial: schema-enforced tool calling replaces free codegen, param validity 20%→98%;
- Enum as capability whitelist: unsupported analysis types blocked at schema level, no fake success.
"""

from __future__ import annotations

import os
from typing import Any

from src.control.intent_vocabulary import ANALYSIS_TYPES
from src.schemas.workflow import TerminalStatus

# Only types WorkflowManager._execute_analysis_workflow truly routes & executes
SUPPORTED_ANALYSIS_TYPES: tuple[str, ...] = tuple(ANALYSIS_TYPES)

# When tool returns these statuses, AgentRuntime immediately bubbles to UI (HITL gate / terminal), no further LLM round
# 唯一来源是 TerminalStatus 枚举，避免与 schemas/workflow.py 漂移。
TERMINAL_STATUSES: frozenset[str] = frozenset(item.value for item in TerminalStatus)

# 工具结果要求模型继续推理而非立即返回的标记字段。
CONTINUE_TOOL_LOOP_FLAG = "continue_tool_loop"

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "run_analysis",
            "description": (
                "Execute omics analysis. Only supports enum types; "
                "input file prefers context's latest downloaded asset, then params. "
                "When no data available, backend returns needs_input; do not fabricate success. "
                "single_cell only runs fixed unfiltered RNA QC and stops for scientific review; "
                "it does not filter, cluster, annotate or approve parameters."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "analysis_type": {
                        "type": "string",
                        "enum": list(SUPPORTED_ANALYSIS_TYPES),
                        "description": "Analysis type",
                    },
                    "question": {
                        "type": "string",
                        "description": "Original user question verbatim, for result interpretation & capsule",
                    },
                },
                "required": ["analysis_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_datasets",
            "description": (
                "Search public databases (GEO/KEGG/UniProt/Reactome/HPA) for candidate datasets, "
                "return candidates for user confirmation; do not claim downloaded before confirmation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search terms, may include GSE accession or keywords",
                    },
                    "sources": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["geo", "kegg", "uniprot", "reactome", "hpa"],
                        },
                        "description": "Limit data sources; omit to search all registered sources",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_knowledge",
            "description": (
                "DEPRECATED legacy fallback answer tool. It is the only knowledge tool "
                "exposed in legacy mode (AGENT_KNOWLEDGE_TOOL is a legacy value); when "
                "search_knowledge is not listed, call this tool normally."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Full question verbatim"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_memory",
            "description": (
                "Query run memory (.wrroc/kg_memory) for historical run Q&A: "
                "past run parameters, steps, outputs (e.g., 'what params did the last "
                "differential expression use'). NOT for biology knowledge — use query_knowledge."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Full question verbatim"},
                },
                "required": ["query"],
            },
        },
    },
]

SEARCH_KNOWLEDGE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_knowledge",
        "description": (
            "Retrieve literature evidence (chunks with E-ids and traceable sources) from the "
            "local knowledge base. This tool does NOT answer the question and does NOT generate "
            "text: continue reasoning from the returned evidence and cite evidence ids as [E#]. "
            "Call it when the user needs literature support."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Literature search query, keeping key genes/methods/terms",
                },
                "scope": {
                    "type": "string",
                    "enum": ["answer", "survey", "exhaustive"],
                    "description": (
                        "answer (default): a specific mechanism or conclusion; "
                        "returns complete evidence passages. "
                        "survey: a topic overview or literature landscape; returns about 20 "
                        "deduplicated sources with at most 2 bounded excerpts each. "
                        "exhaustive: a paged literature inventory; returns at most 50 sources "
                        "per page and explicitly does not claim completeness."
                    ),
                },
                "max_evidence": {
                    "type": "integer",
                    "minimum": 1,
                    "description": (
                        "Requested count: scope=answer evidence entries (default 8, hard cap 12); "
                        "survey sources (default 20, hard cap 30); exhaustive sources per page "
                        "(default 50, hard cap 50)."
                    ),
                },
                "cursor": {
                    "type": "string",
                    "description": (
                        "Opaque next_cursor from a previous exhaustive page; only valid for the "
                        "same query and scope."
                    ),
                },
                "sources_only": {
                    "type": "boolean",
                    "description": (
                        "exhaustive only: return a source list without evidence text; fetch a "
                        "specific source later with source_id."
                    ),
                },
                "source_id": {
                    "type": "string",
                    "description": (
                        "exhaustive only: return the full text chunks of one source from the "
                        "preceding source list; pass the page's next_cursor when continuing."
                    ),
                },
            },
            "required": ["query"],
        },
    },
}


LEGACY_KNOWLEDGE_TOOL_VALUES = frozenset(
    {"legacy", "query_knowledge", "off", "0", "false", "no", "disabled"}
)


def knowledge_tool_enabled(*, force: bool = False) -> bool:
    """文献证据工具默认开启；``AGENT_KNOWLEDGE_TOOL`` 设为 legacy 等值可回退旧工具。"""
    if force:
        return True
    raw = os.getenv("AGENT_KNOWLEDGE_TOOL", "").strip().lower()
    return raw not in LEGACY_KNOWLEDGE_TOOL_VALUES


def build_tool_schemas() -> list[dict[str, Any]]:
    """返回本轮可用工具清单；开关关闭时与既有清单完全一致。

    开启期间知识工具只暴露 ``search_knowledge``：与 ``query_knowledge`` 并存会让
    模型在“知识问答”和“文献证据”之间摇摆；旧工具只在 legacy 回退期保留。
    """
    if not knowledge_tool_enabled():
        return list(TOOL_SCHEMAS)
    # query_memory 的描述只在开启期改写，legacy 回退保持原样。
    schemas: list[dict[str, Any]] = []
    for schema in TOOL_SCHEMAS:
        name = schema["function"]["name"]
        if name == "query_knowledge":
            continue
        if name == "query_memory":
            schema = {
                **schema,
                "function": {
                    **schema["function"],
                    "description": schema["function"]["description"].replace(
                        "use query_knowledge", "use the literature tool (search_knowledge)"
                    ),
                },
            }
        schemas.append(schema)
    return schemas + [SEARCH_KNOWLEDGE_SCHEMA]


SYSTEM_PROMPT = """You are CellSpatio — single-cell & spatial omics analysis agent.

Strictly follow these rules to choose actions:
1. User wants to run analysis (differential expression / single-cell clustering / spatial transcriptomics) → call run_analysis, analysis_type MUST be from enum.
2. User wants to search or download public data (GEO/KEGG/UniProt) → call search_datasets.
3. User asks biology knowledge, methods, gene/pathway meaning → call query_knowledge.
4. User asks about past/historical runs (parameters, steps, outputs, "上次/之前/历史运行") → call query_memory.
5. Chit-chat, greetings, unrelated → do NOT call tools, reply briefly.

Constraints:
- At most ONE tool call per turn; tool results returned by system, you MUST NOT fabricate tool outputs.
- When user requests unsupported analysis type (e.g., pathway enrichment, volcano plot standalone), do NOT call tool; directly inform of three supported types.
- Tool returning needs_* status means waiting for user confirmation or more input; forward that status's message to user directly.
- single_cell runs only unfiltered human RNA QC; never claim filtering, clustering, annotation or scientific acceptance.
"""

KNOWLEDGE_TOOL_PROMPT = """

## Literature evidence tool
Rule 3 resolves to search_knowledge: query_knowledge is not exposed while this tool is enabled.
6. When the user needs literature support (mechanism, gene function, methodology) → call
   search_knowledge first. The tool returns only evidence chunks with E-ids and sources, never a
   final answer: keep reasoning yourself and cite the evidence ids inline as [E#].
- For a specific mechanism or conclusion, use scope=answer (default). Do not request more than
  12 evidence items; use max_evidence only when the question genuinely needs more than 8.
- For a topic overview, comparison, or list of relevant literature, use scope=survey. It returns
  about 20 deduplicated sources with short excerpts, not full texts; do not claim it is exhaustive.
- For a paged inventory, use scope=exhaustive. It returns at most 50 sources per page with
  has_more/next_cursor; continue only with the returned cursor and never claim the list is complete.
  Use sources_only=true to inspect the source list, then source_id to read one source's text.
- If retrieval fails or the service is unavailable, say so truthfully; never claim "there is no
   evidence" without a successful empty retrieval.
- Optional structured claims: when you want the UI to list key assertions, append one
  ```claims fenced JSON array such as [{"text": "...", "evidence_ids": ["E1"]}]. Include only
  claims you actually assert from the cited evidence; omit the block when unsure.
"""


def build_system_prompt() -> str:
    """返回本轮系统提示；检索工具开关决定是否附加文献取证规则。"""
    if knowledge_tool_enabled():
        return SYSTEM_PROMPT + KNOWLEDGE_TOOL_PROMPT
    return SYSTEM_PROMPT
