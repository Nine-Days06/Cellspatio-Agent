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


def test_knowledge_tool_is_opt_in_and_does_not_answer(monkeypatch):
    """默认不暴露检索工具；只有 AGENT_KNOWLEDGE_TOOL=search 才加入且声明不生成答案。"""
    from src.control.tools import build_system_prompt, build_tool_schemas

    monkeypatch.delenv("AGENT_KNOWLEDGE_TOOL", raising=False)
    assert "search_knowledge" not in {schema["function"]["name"] for schema in build_tool_schemas()}
    assert "search_knowledge" not in build_system_prompt()

    monkeypatch.setenv("AGENT_KNOWLEDGE_TOOL", "search")
    schemas = {schema["function"]["name"]: schema for schema in build_tool_schemas()}
    tool = schemas["search_knowledge"]

    assert "query_knowledge" not in schemas
    assert tool["function"]["parameters"]["required"] == ["query"]
    assert "does NOT answer" in tool["function"]["description"]
    assert "search_knowledge" in build_system_prompt()
    assert "query_knowledge is not exposed" in build_system_prompt()
    assert "[E#]" in build_system_prompt()


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


def test_stream_terminal_statuses_are_terminal_subset():
    """流式终态必须由同一枚举派生，不能是第二份手写清单。"""
    from src.control.agent_runtime import STREAM_TERMINAL_STATUSES
    from src.control.tools import TERMINAL_STATUSES

    assert STREAM_TERMINAL_STATUSES < TERMINAL_STATUSES
    assert TERMINAL_STATUSES - STREAM_TERMINAL_STATUSES == {"needs_input", "no_results"}


def test_search_datasets_enum_includes_new_sources():
    from src.control.tools import TOOL_SCHEMAS

    sd = next(t for t in TOOL_SCHEMAS if t["function"]["name"] == "search_datasets")
    enum = sd["function"]["parameters"]["properties"]["sources"]["items"]["enum"]
    assert {"geo", "kegg", "uniprot", "reactome", "hpa"} <= set(enum)
