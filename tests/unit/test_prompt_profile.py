"""prompt_profile：领域提示词加载、幂等注入与 format 安全。"""

import pytest

from src.knowledge import prompt_profile


def test_load_entity_types_from_file():
    text = prompt_profile.load_entity_types()
    assert "CellType:" in text and "Gene:" in text
    assert "NEVER build an identifier from a symbol" in text
    assert "Person" not in text and "Publication" not in text


def test_load_domain_rules_has_mapping_rule_and_examples():
    text = prompt_profile.load_domain_rules()
    assert text.startswith("---Domain Rules")
    assert "mention => NCBIGene:NNNN" in text
    assert "Worked Examples" in text


def test_prompt_files_have_no_format_braces():
    """系统提示词随后被 .format()，字面 { } 会 KeyError。"""
    for text in (prompt_profile.load_entity_types(), prompt_profile.load_domain_rules()):
        assert "{" not in text and "}" not in text


def test_apply_domain_rules_is_idempotent():
    prompts = {
        "entity_extraction_system_prompt": "BASE",
        "entity_extraction_json_system_prompt": "BASE",
    }
    prompt_profile.apply_domain_rules(prompts)
    first = prompts["entity_extraction_system_prompt"]
    assert first.startswith("BASE") and "---Domain Rules" in first
    prompt_profile.apply_domain_rules(prompts)
    assert prompts["entity_extraction_system_prompt"] == first
    assert "---Domain Rules" in prompts["entity_extraction_json_system_prompt"]


def test_load_entity_types_missing_file_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(prompt_profile, "PROMPTS_DIR", tmp_path)
    with pytest.raises(FileNotFoundError):
        prompt_profile.load_entity_types()
