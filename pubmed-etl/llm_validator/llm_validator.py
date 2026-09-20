"""LLM 文献验证模块 - 支持多供应商、批量验证、断点续传"""
import json
import re
import time
from pathlib import Path
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from config.settings import (
    LLM_PROVIDER, LLM_PROVIDER_CONFIGS,
    LLM_BATCH_SIZE, LLM_CONCURRENCY, LLM_MAX_RETRIES,
    OUTPUT_DIR,
)
from utils.db import get_conn, now_iso
from config.settings import DB_PATH


CHECKPOINT_FILENAME = "llm_validation_progress.json"


def _checkpoint_path() -> Path:
    return Path(OUTPUT_DIR) / CHECKPOINT_FILENAME


def _save_checkpoint(round_num: int, failed_rows: list):
    """保存检查点"""
    data = {
        "round": round_num,
        "failed": [dict(r) for r in failed_rows],
        "updated_at": datetime.now().isoformat(),
    }
    path = _checkpoint_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"  检查点已保存: 第 {round_num} 轮, 待重试 {len(failed_rows)} 篇")


def _load_checkpoint() -> tuple[int | None, list]:
    """加载检查点"""
    from datetime import datetime
    path = _checkpoint_path()
    if not path.exists():
        return None, []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data["round"], data["failed"]
    except Exception as e:
        print(f"  检查点读取失败: {e}")
        return None, []


def _clear_checkpoint():
    """删除检查点"""
    path = _checkpoint_path()
    if path.exists():
        path.unlink()
        print("  检查点已清除")


def _extract_json(text: str, fix_glm_multi_array: bool = False) -> list | None:
    """从 LLM 响应中提取 JSON 数组"""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1]
        s = s.rsplit("```", 1)[0] if "```" in s else s
        s = s.strip()

    try:
        data = json.loads(s)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    start = s.find("[")
    if start == -1:
        return None
    json_str = s[start:]

    if fix_glm_multi_array:
        normalized = json_str.replace('\r\n', '\n').replace('\n', '')
        fixed = re.sub(r',\s*\]', ']', re.sub(r'\],\s*\[', ',', normalized))
        if fixed != normalized:
            try:
                data = json.loads(fixed)
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass

    # 尝试补全截断的 JSON
    if json_str.startswith('[') and not json_str.rstrip().endswith(']'):
        fixed2 = json_str.rstrip().rstrip(',') + '\n]'
        if fixed2 != json_str:
            try:
                data = json.loads(fixed2)
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass

    return None


# ── LLM 调用 ──────────────────────────────────────────────────

def _get_client_and_model():
    """获取当前 LLM 客户端和模型名"""
    from openai import OpenAI

    config = LLM_PROVIDER_CONFIGS[LLM_PROVIDER]

    if config["client_type"] == "zhipuai":
        import zhipuai
        client = zhipuai.ZhipuAI(api_key=config["api_key"])
    else:
        client = OpenAI(
            api_key=config["api_key"],
            base_url=config["base_url"],
        )

    return client, config["model"], config


def _call_llm(messages: list, temperature: float = 0) -> str:
    """调用 LLM API"""
    client, model, config = _get_client_and_model()

    kwargs = config.get("extra_kwargs", {})
    kwargs["temperature"] = temperature

    extra_body = config.get("extra_body", {})

    for attempt in range(1, LLM_MAX_RETRIES + 1):
        try:
            if config["client_type"] == "zhipuai":
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    **kwargs,
                )
            else:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    extra_body=extra_body if extra_body else None,
                    **kwargs,
                )
            return response.choices[0].message.content
        except Exception as e:
            if attempt == LLM_MAX_RETRIES:
                raise
            time.sleep(2 ** attempt)
            print(f"    LLM 调用失败，重试 {attempt}/{LLM_MAX_RETRIES}: {e}")

    return ""


# ── 验证逻辑 ──────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "你是一个人类多组学（Human Multi-omics）分析领域的文献筛选专家。\n"
    "本项目的目标是从 PubMed 文献中筛选与人类多组学研究相关的高质量文献。\n"
    "多组学包括：转录组学（Transcriptomics）、蛋白质组学（Proteomics）、"
    "代谢组学（Metabolomics）、基因组学（Genomics）、表观基因组学（Epigenomics）。\n"
    "判断以下每篇文献的摘要是否与人类多组学分析相关。\n\n"
    "判断标准：\n"
    "- 涉及人类疾病/组织/细胞的组学研究 → RELEVANT\n"
    "- 涉及组学技术方法开发（且以人类研究为主要应用）→ RELEVANT\n"
    "- 涉及多组学数据整合分析 → RELEVANT\n"
    "- 仅泛泛提及组学概念，无具体研究内容 → NOT_RELEVANT\n"
    "- 研究非人类物种 → NOT_RELEVANT\n"
    "- 传统临床/流行病学（无组学数据）→ NOT_RELEVANT\n\n"
    "请以 JSON 数组格式返回结果，每个元素包含：\n"
    '{"pmid": "xxx", "verdict": "RELEVANT/NOT_RELEVANT", "reason": "判断理由"}\n'
)


def _build_user_prompt(articles: list) -> str:
    """构建用户提示词"""
    parts = ["请判断以下文献是否与人类多组学分析相关：\n"]
    for i, art in enumerate(articles, 1):
        parts.append(f"文献 {i}:")
        parts.append(f"PMID: {art.get('pmid', '')}")
        parts.append(f"标题: {art.get('title', '')}")
        parts.append(f"摘要: {art.get('abstract', '')[:500]}...")
        parts.append("")
    return "\n".join(parts)


def _validate_batch(articles: list) -> list:
    """验证一批文献"""
    from datetime import datetime

    user_prompt = _build_user_prompt(articles)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    fix_multi = LLM_PROVIDER_CONFIGS[LLM_PROVIDER].get("fix_multi_array", False)
    response = _call_llm(messages)
    results = _extract_json(response, fix_glm_multi_array=fix_multi)

    if results is None:
        # 解析失败，标记为未知
        return [
            {"pmid": art.get("pmid"), "verdict": "UNKNOWN", "reason": "LLM 响应解析失败"}
            for art in articles
        ]

    # 确保每个结果都有 pmid
    validated = []
    for r in results:
        if "pmid" not in r:
            # 尝试从 articles 中匹配
            idx = len(validated)
            if idx < len(articles):
                r["pmid"] = articles[idx].get("pmid")
        validated.append(r)

    return validated


# ── 主流程 ────────────────────────────────────────────────────

def run_validation(batch_mode: bool = False, db_path: Path = None):
    """运行 LLM 验证"""
    from datetime import datetime

    db_path = db_path or DB_PATH

    print("=" * 60)
    print("阶段四：LLM 验证")
    print(f"  供应商: {LLM_PROVIDER}")
    print("=" * 60)

    # 获取待验证文献
    with get_conn(db_path) as conn:
        # 获取通过硬过滤且未验证的文献
        rows = conn.execute("""
            SELECT a.* FROM articles a
            WHERE NOT EXISTS (SELECT 1 FROM filter_log f WHERE f.pmid = a.pmid)
            AND NOT EXISTS (SELECT 1 FROM llm_validation v WHERE v.pmid = a.pmid)
        """).fetchall()

    if not rows:
        print("  无待验证文献")
        return

    print(f"  待验证文献: {len(rows)} 篇")

    # 检查是否有断点
    round_num, failed_rows = _load_checkpoint()
    if failed_rows:
        print(f"  从断点恢复: 第 {round_num} 轮, 待重试 {len(failed_rows)} 篇")
        articles_to_validate = failed_rows
    else:
        articles_to_validate = [dict(r) for r in rows] if 'rows' in dir() else []

    if not articles_to_validate:
        with get_conn(db_path) as conn:
            rows = conn.execute("""
                SELECT a.* FROM articles a
                WHERE NOT EXISTS (
                    SELECT 1 FROM filter_log f WHERE f.pmid = a.pmid
                )
                AND NOT EXISTS (
                    SELECT 1 FROM llm_validation v WHERE v.pmid = a.pmid
                )
            """).fetchall()
            articles_to_validate = [dict(r) for r in rows]

    print(f"  待验证: {len(articles_to_validate)} 篇")

    # 分批验证
    all_results = []
    total_batches = (len(articles_to_validate) + LLM_BATCH_SIZE - 1) // LLM_BATCH_SIZE

    for batch_idx in range(0, len(articles_to_validate), LLM_BATCH_SIZE):
        batch = articles_to_validate[batch_idx:batch_idx + LLM_BATCH_SIZE]
        batch_num = batch_idx // LLM_BATCH_SIZE + 1

        print(f"  验证批次 {batch_num}/{total_batches} ({len(batch)} 篇)...")
        results = _validate_batch(batch)
        all_results.extend(results)

        # 写入数据库
        with get_conn(db_path) as conn:
            for r in results:
                conn.execute("""
                    INSERT OR REPLACE INTO llm_validation (pmid, llm_verdict, reason, validated_at)
                    VALUES (?, ?, ?, ?)
                """, (r.get("pmid"), r.get("verdict", "UNKNOWN"), r.get("reason", ""), now_iso()))

        time.sleep(1)  # 避免限流

    # 统计结果
    relevant = sum(1 for r in all_results if r.get("verdict") == "RELEVANT")
    not_relevant = sum(1 for r in all_results if r.get("verdict") == "NOT_RELEVANT")
    unknown = sum(1 for r in all_results if r.get("verdict") not in ("RELEVANT", "NOT_RELEVANT"))

    print(f"\n  验证完成:")
    print(f"    RELEVANT:     {relevant} 篇")
    print(f"    NOT_RELEVANT: {not_relevant} 篇")
    print(f"    UNKNOWN:      {unknown} 篇")

    _clear_checkpoint()

    return {
        "total": len(all_results),
        "relevant": relevant,
        "not_relevant": not_relevant,
        "unknown": unknown,
    }
