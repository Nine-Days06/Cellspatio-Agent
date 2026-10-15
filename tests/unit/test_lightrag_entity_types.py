"""LightRAG 领域接线测试：addon_params 与提示词注入（不初始化真实 LLM）。"""

from src.knowledge import lightrag_client, prompt_profile


def test_build_addon_params_is_english_and_from_file():
    params = lightrag_client.build_addon_params()
    assert params["language"] == "English"
    assert params["entity_types_guidance"] == prompt_profile.load_entity_types()
    assert "Person" not in params["entity_types_guidance"]
    assert "Publication" not in params["entity_types_guidance"]


def test_install_domain_prompts_injects_once_and_keeps_format_safe():
    from lightrag.prompt import PROMPTS

    lightrag_client.install_domain_prompts()
    first = PROMPTS["entity_extraction_system_prompt"]
    assert "---Domain Rules" in first
    lightrag_client.install_domain_prompts()  # 幂等
    assert PROMPTS["entity_extraction_system_prompt"] == first
    # 注入后仍能 .format()（证明提示词未含字面大括号）
    PROMPTS["entity_extraction_system_prompt"].format(
        tuple_delimiter="<|#|>",
        completion_delimiter="<|COMPLETE|>",
        entity_types_guidance="x",
        examples="y",
        language="English",
        max_total_records=1,
        max_entity_records=1,
    )
