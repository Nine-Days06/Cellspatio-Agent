"""Agent tool contract: schemas, terminal statuses, system prompt.

Design rationale (2026 SOTA alignment):
- ChatSpatial: schema-enforced tool calling replaces free codegen, param validity 20%→98%;
- Enum as capability whitelist: unsupported analysis types blocked at schema level, no fake success.
"""
from __future__ import annotations

from typing import Any

# Only types WorkflowManager._execute_analysis_workflow truly routes & executes
SUPPORTED_ANALYSIS_TYPES: tuple[str, ...] = (
    "differential_expression",
    "single_cell",
    "spatial",
)

# When tool returns these statuses, AgentRuntime immediately bubbles to UI (HITL gate / terminal), no further LLM round
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        "success",
        "error",
        "needs_confirmation",
        "needs_script_confirmation",
        "needs_input",
        "no_results",
    }
)

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "run_analysis",
            "description": (
                "Execute omics analysis. Only supports enum types; "
                "input file prefers context's latest downloaded asset, then params. "
                "When no data available, backend returns needs_input; do not fabricate success."
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
                "Search public databases (GEO/KEGG/UniProt) for candidate datasets, "
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
                            "enum": ["geo", "kegg", "uniprot"],
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
            "description": "Query LightRAG knowledge base for gene/pathway/disease/methods biology Q&A.",
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

SYSTEM_PROMPT = """You are CellSpatio — single-cell & spatial omics analysis agent.

Strictly follow these rules to choose actions:
1. User wants to run analysis (differential expression / single-cell clustering / spatial transcriptomics) → call run_analysis, analysis_type MUST be from enum.
2. User wants to search or download public data (GEO/KEGG/UniProt) → call search_datasets.
3. User asks biology knowledge, methods, gene/pathway meaning → call query_knowledge.
4. Chit-chat, greetings, unrelated → do NOT call tools, reply briefly.

Constraints:
- At most ONE tool call per turn; tool results returned by system, you MUST NOT fabricate tool outputs.
- When user requests unsupported analysis type (e.g., pathway enrichment, volcano plot standalone), do NOT call tool; directly inform of three supported types.
- Tool returning needs_* status means waiting for user confirmation or more input; forward that status's message to user directly.
"""