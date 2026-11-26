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


def test_knowledge_tool_defaults_on_and_keeps_legacy_fallback(monkeypatch):
    """检索工具默认开启；设置 legacy 回退旧工具，且工具声明不生成答案。"""
    from src.control.tools import build_system_prompt, build_tool_schemas

    monkeypatch.delenv("AGENT_KNOWLEDGE_TOOL", raising=False)
    default_names = {schema["function"]["name"] for schema in build_tool_schemas()}
    assert "search_knowledge" in default_names
    assert "query_knowledge" not in default_names
    assert "search_knowledge" in build_system_prompt()

    monkeypatch.setenv("AGENT_KNOWLEDGE_TOOL", "legacy")
    legacy_names = {schema["function"]["name"] for schema in build_tool_schemas()}
    assert legacy_names == {"run_analysis", "search_datasets", "query_knowledge", "query_memory"}
    assert "search_knowledge" not in build_system_prompt()
    assert "call query_knowledge" in build_system_prompt()
    legacy_memory = next(t for t in build_tool_schemas() if t["function"]["name"] == "query_memory")
    assert "use query_knowledge" in legacy_memory["function"]["description"]
    legacy_tool = next(
        schema for schema in build_tool_schemas() if schema["function"]["name"] == "query_knowledge"
    )
    assert "DEPRECATED" in legacy_tool["function"]["description"]
    assert "legacy value" in legacy_tool["function"]["description"]

    monkeypatch.setenv("AGENT_KNOWLEDGE_TOOL", "search")
    schemas = {schema["function"]["name"]: schema for schema in build_tool_schemas()}
    tool = schemas["search_knowledge"]

    assert "query_knowledge" not in schemas
    assert tool["function"]["parameters"]["required"] == ["query"]
    assert "does NOT answer" in tool["function"]["description"]
    assert "search_knowledge" in build_system_prompt()
    assert "query_knowledge is not exposed" in build_system_prompt()
    assert "[E#]" in build_system_prompt()
    assert (
        "use the literature tool (search_knowledge)"
        in schemas["query_memory"]["function"]["description"]
    )


def test_legacy_fallback_value_set_is_explicit(monkeypatch):
    """回退值集合是唯一开关契约：拼错的值按默认开启处理，不能静默回退。"""
    from src.control.tools import LEGACY_KNOWLEDGE_TOOL_VALUES, knowledge_tool_enabled

    assert LEGACY_KNOWLEDGE_TOOL_VALUES == {
        "legacy",
        "query_knowledge",
        "off",
        "0",
        "false",
        "no",
        "disabled",
    }
    for value in LEGACY_KNOWLEDGE_TOOL_VALUES:
        monkeypatch.setenv("AGENT_KNOWLEDGE_TOOL", value.upper())
        assert knowledge_tool_enabled() is False
    monkeypatch.setenv("AGENT_KNOWLEDGE_TOOL", "legacyy")
    assert knowledge_tool_enabled() is True


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


def test_search_knowledge_schema_exposes_all_scopes_and_paging_fields():
    """检索工具暴露三档范围、证据条数与分页/指定来源参数。"""
    from src.control.tools import build_tool_schemas

    schema = next(
        item for item in build_tool_schemas() if item["function"]["name"] == "search_knowledge"
    )
    props = schema["function"]["parameters"]["properties"]

    assert props["scope"]["enum"] == ["answer", "survey", "exhaustive"]
    assert props["max_evidence"]["minimum"] == 1
    assert props["cursor"]["type"] == "string"
    assert props["sources_only"]["type"] == "boolean"
    assert props["source_id"]["type"] == "string"


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
