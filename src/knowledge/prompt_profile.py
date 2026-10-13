"""领域提示词加载器：entity_types/domain_rules 读取与幂等注入抽取系统提示词。

提示词文件禁含字面大括号——系统提示词随后被 .format(**context_base)，
大括号会触发 KeyError（LightRAG operate.py）。
"""

from pathlib import Path
from typing import Any

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

_DOMAIN_MARKER = "---Domain Rules"
_INJECT_KEYS = ("entity_extraction_system_prompt", "entity_extraction_json_system_prompt")


def load_entity_types() -> str:
    """读取领域实体类型列表（英文，含修正的 Gene 行，无 Person/Publication）"""
    return (PROMPTS_DIR / "entity_types.txt").read_text(encoding="utf-8").strip()


def load_domain_rules() -> str:
    """读取领域抽取规则（含基因映射用法与正反例；禁含字面大括号）"""
    return (PROMPTS_DIR / "domain_rules.txt").read_text(encoding="utf-8").strip()


def apply_domain_rules(prompts: dict[str, Any]) -> None:
    """把领域规则幂等注入抽取系统提示词（按 ---Domain Rules 标记判重）"""
    rules = load_domain_rules()
    for key in _INJECT_KEYS:
        base = prompts.get(key)
        if base and _DOMAIN_MARKER not in base:
            prompts[key] = base.rstrip() + "\n\n" + rules + "\n"
