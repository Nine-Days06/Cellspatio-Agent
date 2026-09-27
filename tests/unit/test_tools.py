"""Tool schema validity: contract shared by AgentRuntime and eval."""


def test_tool_names_and_required_fields():
    from src.control.tools import TOOL_SCHEMAS

    names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    assert names == {"run_analysis", "search_datasets", "query_knowledge", "query_memory"}
    for tool in TOOL_SCHEMAS:
        fn = tool["function"]
        assert tool["type"] == "function"
        assert fn["description"].strip()
        assert fn["parameters"]["type"] == "object"
        assert fn["parameters"]["required"]


def test_query_memory_schema_requires_query_param():
    from src.control.tools import SYSTEM_PROMPT, TOOL_SCHEMAS

    mem = next(t for t in TOOL_SCHEMAS if t["function"]["name"] == "query_memory")
    assert mem["function"]["parameters"]["required"] == ["query"]
    assert "query" in mem["function"]["parameters"]["properties"]
    # LLM 必须知道何时选记忆查询而非领域知识查询
    assert "query_memory" in SYSTEM_PROMPT


def test_run_analysis_enum_only_supported_types():
    from src.control.tools import SUPPORTED_ANALYSIS_TYPES, TOOL_SCHEMAS

    run = next(t for t in TOOL_SCHEMAS if t["function"]["name"] == "run_analysis")
    enum = run["function"]["parameters"]["properties"]["analysis_type"]["enum"]
    assert list(SUPPORTED_ANALYSIS_TYPES) == [
        "differential_expression",
        "single_cell",
        "spatial",
    ]
    assert enum == list(SUPPORTED_ANALYSIS_TYPES)
    assert "pathway_analysis" not in enum
    assert "visualization" not in enum


def test_terminal_statuses_cover_hitl_and_outcomes():
    from src.control.tools import TERMINAL_STATUSES

    assert TERMINAL_STATUSES >= {
        "success",
        "error",
        "needs_confirmation",
        "needs_script_confirmation",
        "needs_input",
        "no_results",
    }