# cleaner/llm_validator.py
"""
LLM 文献验证模块
使用 OpenAI 兼容 API（DeepSeek 等）对文献进行二次相关性验证。
"""

import json
import re
import time
import csv
import os
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import OpenAI

from config.settings import (
    DB_PATH, OUTPUT_DIR, LOG_DIR,
    LLM_BATCH_SIZE, LLM_CONCURRENCY, LLM_MAX_TOKENS, LLM_MAX_RETRIES, LLM_MAX_ROUNDS,
    LLM_PROVIDER, LLM_PROVIDER_CONFIGS,
    LLM_BATCH_POLL_INTERVAL, LLM_BATCH_TIMEOUT, LLM_BATCH_AUTO_DELETE,
    ZHIPU_API_KEY, ZHIPU_BATCH_MODEL,
)
from utils import now_iso
from utils.db import get_conn
from utils.logger import get_logger

logger = get_logger("llm_validator", log_dir=LOG_DIR)

CHECKPOINT_FILENAME = "llm_validation_progress.json"


def _checkpoint_path() -> Path:
    return Path(OUTPUT_DIR) / CHECKPOINT_FILENAME


def _save_checkpoint(round_num: int, failed_rows: list):
    """保存轮次检查点，崩溃后可恢复"""
    data = {
        "round": round_num,
        "failed": [dict(r) for r in failed_rows],
        "updated_at": datetime.now().isoformat(),
    }
    path = _checkpoint_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    logger.info(f"检查点已保存: 第 {round_num} 轮, 待重试 {len(failed_rows)} 篇")


def _load_checkpoint() -> tuple[int | None, list]:
    """加载检查点，返回 (round_num, failed_rows) 或 (None, [])"""
    path = _checkpoint_path()
    if not path.exists():
        return None, []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data["round"], data["failed"]
    except Exception as e:
        logger.warning(f"检查点读取失败，将从头开始: {e}")
        return None, []


def _clear_checkpoint():
    """验证全部完成后删除检查点"""
    path = _checkpoint_path()
    if path.exists():
        path.unlink()
        logger.info("检查点已清除（全部验证完成）")


def _extract_json(text: str, fix_glm_multi_array: bool = False) -> list | None:
    """多策略从 LLM 响应中提取 JSON 数组，返回 None 表示全部失败"""
    # 1) 剥离 markdown 代码块标记
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1]
        s = s.rsplit("```", 1)[0] if "```" in s else s
        s = s.strip()

    # 2) 快速路径: 解析完整字符串
    try:
        data = json.loads(s)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    # 3) 从第一个 [ 开始
    start = s.find("[")
    if start == -1:
        return None
    json_str = s[start:]

    if fix_glm_multi_array:
        logger.debug(f"s前50字符: {s[:50]!r}")
        logger.debug(f"s后50字符: {s[-50:]!r}")
        logger.debug(f"json_str前300字符: {json_str[:300]!r}")
        logger.debug(f"json_str末100字符: {json_str[-100:]!r}")
        # 4) 修复多数组 + 尾逗号（先跑，保留所有记录）
        normalized = json_str.replace('\r\n', '\n').replace('\n', '')
        fixed = re.sub(r',\s*\]', ']', re.sub(r'\],\s*\[', ',', normalized))
        if fixed != normalized:
            try:
                data = json.loads(fixed)
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass
        # 5) 尝试补全被截断的 JSON
        if json_str.startswith('[') and not json_str.rstrip().endswith(']'):
            fixed2 = json_str.rstrip().rstrip(',') + '\n]'
            if fixed2 != json_str:
                try:
                    data = json.loads(fixed2)
                    if isinstance(data, list):
                        return data
                except json.JSONDecodeError:
                    pass

    # 6) raw_decode 总回退（跳过 JSON 后的垃圾内容，只返回第一个数组）
    try:
        decoder = json.JSONDecoder()
        data, idx = decoder.raw_decode(json_str)
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    return None


SYSTEM_PROMPT_COMMON = (
    """你是一个人类单细胞与空间/时序组学文献筛选专家。
你的任务是判断每篇 PubMed 文献的摘要是否真正与人类单细胞或空间/时序组学相关。

【什么是单细胞与时空组学？】
单细胞组学：在单细胞分辨率测量转录组/基因组等（如 scRNA-seq、snRNA-seq、CITE-seq）。
空间组学：保留组织空间位置的原位测量（如 Visium、MERFISH、Slide-seq、spatial transcriptomics）。
时序/轨迹：发育或扰动过程中的细胞状态动态（pseudotime、lineage tracing 与单细胞结合）。
关键特征：数据来自单细胞分辨率或空间位置分辨率，或针对这类数据的方法/资源/应用。

【收录（✓）】
1. 人类单细胞转录组/多模态单细胞（含疾病、发育、免疫微环境）
2. 人类空间转录组/空间多组学（含 Visium、MERFISH 等）
3. 单细胞+空间联合分析、细胞通讯、轨迹/时序分析（人类）
4. 面向单细胞/空间数据的方法、算法、工具、数据库（基准可用人类数据）
5. 临床/肿瘤微环境等应用场景中的人类单细胞或空间研究

【排除（✗）】
1. 纯 bulk 组织转录组且无单细胞/空间分辨率
2. 非人类模式生物且无人类数据/方法验证（方法论文可用人类数据则收录）
3. 仅基因/蛋白功能实验、无单细胞或空间组学数据
4. 与生物学无关的纯 ML/工程论文（除非明确针对单细胞/空间组学）
5. 综述/社论/会议摘要视任务配置决定，默认排除无法提取数据描述的类型

【判断步骤】
1. 摘要是否出现单细胞或空间/时序组学技术关键词？
2. 是否为人类（或开发人类方法学）？
3. 是否有可提取的实验/数据/实体信息（供知识图谱）？
4. 综合给出 verdict: RELEVANT 或 NOT_RELEVANT，并给一句中文理由。

"""
)

# 同步批量模式：一次请求判断多篇，返回结果数组
SYSTEM_PROMPT_SYNC = SYSTEM_PROMPT_COMMON + (
    """【输出格式】
请以 JSON 对象格式逐条回答，必须覆盖本次输入的全部文献、不得遗漏，不要包含其他内容：
{"results":[{"pmid":"...","verdict":"RELEVANT 或 NOT_RELEVANT",
"reason":"请用中文简要说明判断依据，指出摘要中的组学类型、整合分析情况和研究对象"}]}
"""
)

# Batch 模式：每篇独立请求，返回单 JSON 对象
SYSTEM_PROMPT_BATCH = SYSTEM_PROMPT_COMMON + (
    """【输出格式】
请仅输出如下 JSON 对象，不要包含其他内容：
{"pmid":"...","verdict":"RELEVANT 或 NOT_RELEVANT",
"reason":"请用中文简要说明判断依据"}
"""
)


INSERT_SQL = """
INSERT INTO llm_validation
    (pmid, llm_verdict, reason, validated_at, human_review)
VALUES (?, ?, ?, ?, NULL)
ON CONFLICT(pmid) DO UPDATE SET
    llm_verdict = excluded.llm_verdict,
    reason = excluded.reason,
    validated_at = excluded.validated_at
"""

UPDATE_HUMAN_REVIEW_SQL = """
UPDATE llm_validation SET human_review = ? WHERE pmid = ?
"""


def _build_batch_prompt(rows: list) -> str:
    """为一批文献构建 prompt 正文"""
    parts = [
        "以下是需要你根据上述标准判断的文献列表，请逐条判断每篇是否与人类单细胞与空间/时序组学相关，"
        "必须覆盖全部文献、不得遗漏：\n\n"
    ]
    for i, row in enumerate(rows, 1):
        title = (row["title"] or "").strip()
        abstract = (row["abstract"] or "").strip()
        parts.append(
            f"## 文献 {i}\nPMID: {row['pmid']}\nTitle: {title}\n"
            f"Abstract: {abstract}\n"
        )
    return "\n".join(parts)


def _build_client() -> tuple:
    """
    根据 LLM_PROVIDER 配置创建 client，返回 (client, model, extra_kwargs, fix_multi_array)。
    失败时返回 (None, None, None, None)。
    """
    cfg = LLM_PROVIDER_CONFIGS.get(LLM_PROVIDER)
    if not cfg:
        logger.error(f"未知的 LLM_PROVIDER: {LLM_PROVIDER}")
        return None, None, None, None

    api_key = os.environ.get(cfg["api_key_env"]) or cfg["api_key_fallback"]
    if not api_key:
        logger.error(f"未设置 {cfg['api_key_env']}（环境变量或 config/settings.py）")
        return None, None, None, None

    if cfg["client_type"] == "zhipuai":
        from zhipuai import ZhipuAI
        client = ZhipuAI(api_key=api_key)
    else:
        client = OpenAI(api_key=api_key, base_url=cfg["base_url"])

    extra_kwargs = {k: v for k, v in cfg["extra_kwargs"].items()}
    extra_kwargs["model"] = cfg["model"]
    extra_body = cfg.get("extra_body")
    if extra_body:
        extra_kwargs["extra_body"] = extra_body

    return client, cfg["model"], extra_kwargs, cfg["fix_multi_array"]


def _call_llm(rows: list) -> tuple[list[dict], list[str]]:
    """调用 LLM API 验证一批文献，返回 (成功结果列表, 失败 PMID 列表)"""
    prompt = _build_batch_prompt(rows)
    last_exception = None
    failed_pmids = [row["pmid"] for row in rows]

    client, model, create_kwargs, fix_multi_array = _build_client()
    if client is None:
        return [], failed_pmids

    response_attr = "choices"

    for attempt in range(1, LLM_MAX_RETRIES + 1):
        try:
            resp = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT_SYNC},
                    {"role": "user", "content": prompt},
                ],
                **create_kwargs,
            )
            choices = getattr(resp, response_attr)
            content = choices[0].message.content.strip()

            result = None
            try:
                data = json.loads(content)
                if isinstance(data, list):
                    result = data
                elif isinstance(data, dict) and "results" in data and isinstance(data["results"], list):
                    result = data["results"]
            except (json.JSONDecodeError, ValueError):
                pass

            if result is None:
                result = _extract_json(content, fix_glm_multi_array=fix_multi_array)
            if result is None:
                raise ValueError(f"无法从 LLM 响应中提取有效 JSON: {content[:200]}")
            return result, []
        except Exception as e:
            last_exception = e
            logger.warning(f"LLM API 调用失败（attempt {attempt}/{LLM_MAX_RETRIES}）: {e}")
            if attempt < LLM_MAX_RETRIES:
                time.sleep(2 ** attempt)

    logger.error(f"LLM API 调用全部失败，跳过该批次: {last_exception}")
    return [], failed_pmids


def run_validation(batch_mode: bool = False):
    """
    LLM 验证路由入口。

    batch_mode=True 且 LLM_PROVIDER=zhipu → 走智谱 Batch API。
    batch_mode=True 但 provider 非 zhipu → 警告后降级同步模式。
    batch_mode=False → 走同步模式（现有逻辑）。
    """
    logger.info("=" * 60)
    logger.info("阶段五：LLM 文献验证")
    logger.info("=" * 60)

    if batch_mode:
        if LLM_PROVIDER != "zhipu":
            logger.warning(
                "Batch API 仅支持智谱提供者（当前 provider=%s），"
                "已自动降级为同步模式", LLM_PROVIDER
            )
            _run_sync_validation()
            return
        _run_zhipu_batch()
        return

    _run_sync_validation()


def _normalize_verdict(value) -> str | None:
    """
    将 LLM 返回的 verdict 归一化为标准值（RELEVANT / NOT_RELEVANT）。
    无法识别（缺失、截断残缺、其他取值）返回 None，由调用方判为失败重试。
    """
    if not value:
        return None
    compact = str(value).strip().upper().replace(" ", "").replace("-", "").replace("_", "")
    if compact == "RELEVANT":
        return "RELEVANT"
    if compact in ("NOTRELEVANT", "IRRELEVANT"):
        return "NOT_RELEVANT"
    return None


def _build_log_rows(batch, pmid_to_result, failed_set, now):
    """
    将一批 LLM 结果整理为待入库 log_rows（4 元组：pmid, verdict, reason, now）
    与失败列表。verdict 缺失或非标准视为解析失败（回退重试），不写 UNKNOWN。
    """
    log_rows = []
    round_failed = []
    for row in batch:
        pmid = row["pmid"]
        if pmid in failed_set:
            round_failed.append(row)
            continue
        r = pmid_to_result.get(pmid)
        if r is None:
            round_failed.append(row)
            continue
        verdict = _normalize_verdict(r.get("verdict"))
        if verdict is None:
            round_failed.append(row)
            continue
        log_rows.append((pmid, verdict, r.get("reason", ""), now))
    return log_rows, round_failed


def _count_verdicts(validated_rows: list) -> dict:
    """统计 log_rows（4 元组: pmid, verdict, reason, now）中各 verdict 数量"""
    verdicts = {}
    for _, v, _, _ in validated_rows:
        verdicts[v] = verdicts.get(v, 0) + 1
    return verdicts


def _run_sync_validation():
    """
    同步多轮 LLM 验证（原 run_validation 逻辑）。
    支持多轮重试 + 检查点恢复。
    跳过已有验证结果的 PMID，输出待人工复核的 CSV。
    """
    # ── 检查点恢复 ──
    start_round, remaining_rows = _load_checkpoint()
    if remaining_rows:
        with get_conn(DB_PATH) as conn:
            done = set(row["pmid"] for row in
                        conn.execute("SELECT pmid FROM llm_validation").fetchall())
        remaining_rows = [r for r in remaining_rows if r["pmid"] not in done]
        logger.info(f"从检查点恢复: 第 {start_round} 轮, "
                    f"待验证 {len(remaining_rows)} 篇")
        if not remaining_rows:
            _clear_checkpoint()
            logger.info("检查点中的文献均已验证，无需继续")
            csv_path = _export_review_csv()
            if csv_path:
                logger.info(f"待复核清单: {csv_path}")
            return
    else:
        with get_conn(DB_PATH) as conn:
            rows = conn.execute("""
                SELECT a.pmid, a.title, a.abstract
                FROM articles a
                WHERE a.abstract IS NOT NULL AND a.abstract != ''
                  AND a.pmid NOT IN (SELECT pmid FROM llm_validation)
                  AND a.pmid NOT IN (
                      SELECT pmid FROM filter_log WHERE stage = 'hard_filter'
                  )
            """).fetchall()

        if not rows:
            logger.info("没有待验证的文献（所有已评分文献均已验证）")
            return

        start_round = 1
        remaining_rows = rows
        logger.info(f"待验证文献: {len(rows)} 篇, "
                    f"{LLM_MAX_ROUNDS + 1} 轮重试机制")

    # ── 多轮重试循环 ──
    all_validated = []
    for round_num in range(start_round, LLM_MAX_ROUNDS + 2):
        if not remaining_rows:
            break

        round_batches = (len(remaining_rows) + LLM_BATCH_SIZE - 1) // LLM_BATCH_SIZE
        logger.info(f"--- 第 {round_num} 轮: {len(remaining_rows)} 篇, "
                    f"{round_batches} 批 ---")

        round_failed = []
        batches = []
        for start in range(0, len(remaining_rows), LLM_BATCH_SIZE):
            batches.append(remaining_rows[start:start + LLM_BATCH_SIZE])
        logger.info(f"  共 {round_batches} 批, 并发 {LLM_CONCURRENCY} 路")

        with ThreadPoolExecutor(max_workers=LLM_CONCURRENCY) as executor:
            future_to_batch = {
                executor.submit(_call_llm, batch): batch
                for batch in batches
            }
            completed = 0
            for future in as_completed(future_to_batch):
                batch = future_to_batch[future]
                batch_num = batches.index(batch) + 1
                try:
                    results, failed_pmids = future.result()
                except Exception as e:
                    logger.error(f"  批次 {batch_num}/{round_batches} 异常: {e}")
                    round_failed.extend(batch)
                    completed += 1
                    continue

                failed_set = set(failed_pmids)
                if results:
                    now = now_iso()
                    pmid_to_result = {r["pmid"]: r for r in results}
                    log_rows, batch_failed = _build_log_rows(
                        batch, pmid_to_result, failed_set, now
                    )
                    round_failed.extend(batch_failed)

                    if log_rows:
                        with get_conn(DB_PATH) as conn:
                            conn.executemany(INSERT_SQL, log_rows)
                        all_validated.extend(log_rows)
                else:
                    round_failed.extend(batch)

                completed += 1
                logger.info(f"  [{completed}/{round_batches}] "
                            f"批次 {batch_num} 完成"
                            f"({'成功' if results else '全部失败'})")

        remaining_rows = round_failed
        logger.info(f"  第 {round_num} 轮完成: 累计成功 {len(all_validated)} 篇, "
                    f"失败 {len(remaining_rows)} 篇")

        # 每轮结束保存检查点
        _save_checkpoint(round_num + 1, remaining_rows)

    # ── 收尾 ──
    _clear_checkpoint()

    if remaining_rows:
        _export_failed_pmids_csv(remaining_rows)
        logger.warning(f"仍有 {len(remaining_rows)} 篇验证失败, 请检查日志")
    else:
        logger.info("所有文献验证成功")

    if not all_validated:
        logger.warning("所有批次均验证失败，请检查 API 配置")
        return

    verdicts = _count_verdicts(all_validated)
    logger.info("LLM 验证统计:")
    for v, c in sorted(verdicts.items()):
        pct = c / len(all_validated) * 100
        logger.info(f"  {v}: {c} 篇 ({pct:.1f}%)")

    csv_path = _export_review_csv()
    logger.info(f"LLM 验证完成，待复核清单: {csv_path}")


def _run_sync_validation_for_pmids(pmid_list: list):
    """对指定的 PMID 列表执行同步多轮验证（用于 batch 降级）"""
    if not pmid_list:
        return
    with get_conn(DB_PATH) as conn:
        placeholders = ",".join("?" for _ in pmid_list)
        rows = conn.execute(f"""
            SELECT pmid, title, abstract
            FROM articles
            WHERE pmid IN ({placeholders})
              AND abstract IS NOT NULL AND abstract != ''
              AND pmid NOT IN (
                  SELECT pmid FROM filter_log WHERE stage = 'hard_filter'
              )
        """, pmid_list).fetchall()
    if not rows:
        logger.info("降级的 PMID 列表中无限有效摘要的文献，跳过")
        return
    logger.info(f"同步降级: 对 {len(rows)} 篇文献执行同步验证")

    rows_list = [dict(r) for r in rows]
    remaining_rows = rows_list
    all_validated = []
    for round_num in range(1, LLM_MAX_ROUNDS + 2):
        if not remaining_rows:
            break
        round_failed = []
        batches = []
        for start in range(0, len(remaining_rows), LLM_BATCH_SIZE):
            batches.append(remaining_rows[start:start + LLM_BATCH_SIZE])
        with ThreadPoolExecutor(max_workers=LLM_CONCURRENCY) as executor:
            future_to_batch = {
                executor.submit(_call_llm, batch): batch
                for batch in batches
            }
            for future in as_completed(future_to_batch):
                batch = future_to_batch[future]
                try:
                    results, failed_pmids = future.result()
                except Exception:
                    round_failed.extend(batch)
                    continue
                failed_set = set(failed_pmids)
                if results:
                    now = now_iso()
                    pmid_to_result = {r["pmid"]: r for r in results}
                    log_rows, batch_failed = _build_log_rows(
                        batch, pmid_to_result, failed_set, now
                    )
                    round_failed.extend(batch_failed)
                    if log_rows:
                        with get_conn(DB_PATH) as conn:
                            conn.executemany(INSERT_SQL, log_rows)
                        all_validated.extend(log_rows)
                else:
                    round_failed.extend(batch)
        remaining_rows = round_failed
    if all_validated:
        logger.info(f"同步降级完成: {len(all_validated)} 篇成功")
    if remaining_rows:
        logger.warning(f"同步降级仍有 {len(remaining_rows)} 篇失败")


def _export_failed_pmids_csv(failed_rows: list) -> Path | None:
    """导出最终验证失败的 PMID 列表"""
    if not failed_rows:
        return None
    out_dir = Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"llm_validation_failed_{ts}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["pmid", "title", "abstract_preview"])
        for row in failed_rows:
            writer.writerow([row["pmid"], row["title"], row["abstract"] or ""])
    logger.warning(f"仍有 {len(failed_rows)} 篇验证失败: {csv_path}")
    return csv_path


def _export_review_csv() -> Path | None:
    """导出所有待人工复核的记录为 CSV"""
    with get_conn(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT v.pmid, a.title, a.abstract,
                   v.llm_verdict, v.reason
            FROM llm_validation v
            JOIN articles a ON a.pmid = v.pmid
            WHERE v.human_review IS NULL
            ORDER BY v.llm_verdict DESC, a.pmid
        """).fetchall()

    if not rows:
        logger.info("没有待复核的记录")
        return None

    out_dir = Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"llm_review_pending_{ts}.csv"

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "pmid", "title", "abstract",
            "llm_verdict", "reason", "human_review",
        ])
        for row in rows:
            writer.writerow([
                row["pmid"],
                row["title"],
                row["abstract"] or "",
                row["llm_verdict"],
                row["reason"],
                "",
            ])

    logger.info(f"待复核清单已导出: {csv_path}")
    logger.info("请人工标注 human_review 列为 Y 或 N（通过/驳回），然后运行 --step import-review")
    return csv_path


# ═══════════════════════════════════════════════════════════════
# 智谱 Batch API（仅 zhipu provider）
# ═══════════════════════════════════════════════════════════════

BATCH_CHECKPOINT_FILE = "llm_batch_progress.json"

PROMPT_PREFIX = (
    "请根据上述标准判断以下文献是否与人类单细胞与空间/时序组学相关：\n\n"
)

PROMPT_OUTPUT_FORMAT = (
    '# 输出格式（仅输出如下 JSON，不要其他文字）：\n'
    '{"pmid": "%s", "verdict": "RELEVANT 或 NOT_RELEVANT", '
    '"reason": "用中文简要说明判断依据"}'
)


def _batch_checkpoint_path() -> Path:
    return Path(OUTPUT_DIR) / BATCH_CHECKPOINT_FILE


def _save_batch_checkpoint(data: dict):
    path = _batch_checkpoint_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    logger.info(f"Batch 检查点已保存: batch_id={data.get('batch_id', 'N/A')}")


def _load_batch_checkpoint() -> dict | None:
    path = _batch_checkpoint_path()
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Batch 检查点读取失败，将重新提交: {e}")
        return None


def _clear_batch_checkpoint():
    path = _batch_checkpoint_path()
    if path.exists():
        path.unlink()
        logger.info("Batch 检查点已清除")


def _build_per_article_prompt(pmid: str, title: str, abstract: str) -> str:
    return (
        f"{PROMPT_PREFIX}"
        f"PMID: {pmid}\n"
        f"Title: {title or ''}\n"
        f"Abstract: {abstract or ''}\n\n"
        f"{PROMPT_OUTPUT_FORMAT % pmid}"
    )


def _build_jsonl(rows: list) -> Path:
    """构建 batch JSONL 文件（每篇一行），返回文件路径"""
    out_dir = Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    jsonl_path = out_dir / f"batch_input_{ts}.jsonl"

    model = ZHIPU_BATCH_MODEL
    system_content = SYSTEM_PROMPT_BATCH

    with open(jsonl_path, "w", encoding="utf-8") as f:
        for row in rows:
            pmid = row["pmid"]
            article_prompt = _build_per_article_prompt(
                pmid, row["title"] or "", row["abstract"] or ""
            )
            req = {
                "custom_id": pmid,
                "method": "POST",
                "url": "/v4/chat/completions",
                "body": {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_content},
                        {"role": "user", "content": article_prompt},
                    ],
                    "temperature": 0,
                    "max_tokens": LLM_MAX_TOKENS,
                },
            }
            f.write(json.dumps(req, ensure_ascii=False) + "\n")

    file_size_mb = jsonl_path.stat().st_size / (1024 * 1024)
    logger.info(f"JSONL 已构建: {jsonl_path} ({len(rows)} 行, {file_size_mb:.2f} MB)")
    return jsonl_path


def _parse_batch_results(jsonl_path: str) -> tuple[int, list[str]]:
    """
    解析 batch 输出结果 JSONL，写入 llm_validation 表。
    返回 (成功数, 失败 PMID 列表)。
    """
    success_count = 0
    failed_pmids = []

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                logger.warning(f"Batch 结果行 {line_num} JSON 解析失败")
                failed_pmids.append("")
                continue

            custom_id = item.get("custom_id", "")
            pmid = custom_id

            resp = item.get("response", {})
            if resp.get("status_code") != 200:
                logger.warning(f"Batch 请求失败: pmid={pmid}, status={resp.get('status_code')}")
                failed_pmids.append(pmid)
                continue

            body = resp.get("body", {})
            choices = body.get("choices", [])
            if not choices:
                failed_pmids.append(pmid)
                continue

            content = choices[0].get("message", {}).get("content", "").strip()
            if not content:
                failed_pmids.append(pmid)
                continue

            # 统一 strip markdown（glm-4-flash 常返回 ```json ... ```）
            clean = content.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[-1]
                clean = clean.rsplit("```", 1)[0] if "```" in clean else clean
                clean = clean.strip()

            # 优先直接解析为单 JSON 对象（batch 每篇独立请求的预期格式）
            obj = None
            try:
                obj = json.loads(clean)
                if not isinstance(obj, dict):
                    obj = None
            except (json.JSONDecodeError, ValueError):
                pass

            # 若失败，回退 _extract_json 处理数组/results 格式
            if obj is None:
                parsed = _extract_json(content, fix_glm_multi_array=True)
                if parsed:
                    obj = parsed[0] if isinstance(parsed, list) else parsed

            if obj is None:
                logger.warning(f"Batch 结果解析失败: pmid={pmid}, content={content[:200]}")
                failed_pmids.append(pmid)
                continue

            verdict = _normalize_verdict(obj.get("verdict"))
            if verdict is None:
                logger.warning(
                    f"Batch 结果 verdict 无法识别（判为失败重试）: "
                    f"pmid={pmid}, verdict={obj.get('verdict')!r}"
                )
                failed_pmids.append(pmid)
                continue
            reason = obj.get("reason", "")
            now = now_iso()

            with get_conn(DB_PATH) as conn:
                conn.execute(INSERT_SQL, (pmid, verdict, reason, now))
            success_count += 1

    return success_count, failed_pmids


def _run_zhipu_batch():
    """智谱 Batch API 主流程：JSONL 构建 → 上传 → 提交 → 轮询 → 下载 → 解析 → 降级"""
    api_key = os.environ.get("ZHIPU_API_KEY") or ZHIPU_API_KEY
    if not api_key:
        logger.error("未设置 ZHIPU_API_KEY，无法使用 Batch API")
        return

    from zhipuai import ZhipuAI
    client = ZhipuAI(api_key=api_key)

    # ── 步骤 1：检查 checkpoint 恢复 ──
    chk = _load_batch_checkpoint()
    if chk:
        batch_id = chk.get("batch_id")
        logger.info(f"发现 Batch 检查点: batch_id={batch_id}, status={chk.get('status')}")
        try:
            batch_status = client.batches.retrieve(batch_id)
            st = batch_status.status
            if st == "completed":
                logger.info(f"Batch 任务已完成，直接下载结果")
                _download_and_parse_batch(client, batch_status, chk)
                _finalize_batch(chk)
                return
            elif st in ("in_progress", "finalizing", "validating"):
                logger.info(f"恢复轮询 batch_id={batch_id}")
                _poll_until_done(client, batch_id, chk)
                _finalize_batch(chk)
                return
            else:
                logger.warning(f"Batch 任务已结束（status={st}），降级为同步模式")
                _clear_batch_checkpoint()
                _run_sync_validation_for_pmids(chk.get("pmid_list", []))
                return
        except Exception as e:
            logger.warning(f"查询 batch 状态失败: {e}，删除检查点并重新提交")
            _clear_batch_checkpoint()

    # ── 步骤 2：加载待验证文献 ──
    with get_conn(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT pmid, title, abstract
            FROM articles
            WHERE abstract IS NOT NULL AND abstract != ''
              AND pmid NOT IN (SELECT pmid FROM llm_validation)
              AND pmid NOT IN (
                  SELECT pmid FROM filter_log WHERE stage = 'hard_filter'
              )
        """).fetchall()

    if not rows:
        logger.info("没有待验证文献")
        return

    rows_list = [dict(r) for r in rows]
    all_pmids = [r["pmid"] for r in rows_list]
    logger.info(f"待验证文献: {len(rows_list)} 篇（Batch 模式）")

    # ── 步骤 3：构建 JSONL ──
    jsonl_path = _build_jsonl(rows_list)

    # ── 步骤 4：上传文件 ──
    logger.info("上传 Batch 文件...")
    try:
        file_obj = client.files.create(
            file=open(jsonl_path, "rb"),
            purpose="batch",
        )
        logger.info(f"文件已上传: {file_obj.id}")
    except Exception as e:
        logger.error(f"上传文件失败: {e}，降级为同步模式")
        _run_sync_validation_for_pmids(all_pmids)
        return

    # ── 步骤 5：创建 Batch 任务 ──
    logger.info("创建 Batch 任务...")
    try:
        batch = client.batches.create(
            input_file_id=file_obj.id,
            endpoint="/v4/chat/completions",
            auto_delete_input_file=LLM_BATCH_AUTO_DELETE,
            metadata={
                "description": "Human multi-omics literature LLM validation",
                "project": "cellspatio-literature-selection",
            },
        )
        logger.info(f"Batch 任务已提交: {batch.id}")
    except Exception as e:
        logger.error(f"创建 Batch 任务失败: {e}，降级为同步模式")
        _run_sync_validation_for_pmids(all_pmids)
        return

    _save_batch_checkpoint({
        "batch_id": batch.id,
        "input_file_id": file_obj.id,
        "pmid_list": all_pmids,
        "status": "active",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    })

    # ── 步骤 6：轮询 ──
    _poll_until_done(client, batch.id, {
        "batch_id": batch.id,
        "input_file_id": file_obj.id,
        "pmid_list": all_pmids,
        "status": "active",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    })

    _finalize_batch({
        "batch_id": batch.id,
        "pmid_list": all_pmids,
    })


def _poll_until_done(client, batch_id: str, chk: dict):
    """轮询 Batch 任务直到完成/失败/超时"""
    import sys

    start_time = time.time()
    while True:
        elapsed = int(time.time() - start_time)
        if elapsed >= LLM_BATCH_TIMEOUT:
            logger.error(f"Batch 任务超时（>{LLM_BATCH_TIMEOUT}s），取消任务并降级同步")
            try:
                client.batches.cancel(batch_id)
            except Exception:
                pass
            _clear_batch_checkpoint()
            _run_sync_validation_for_pmids(chk.get("pmid_list", []))
            return

        try:
            status = client.batches.retrieve(batch_id)
        except Exception as e:
            logger.error(f"查询 Batch 状态失败: {e}")
            time.sleep(LLM_BATCH_POLL_INTERVAL)
            continue

        st = status.status
        counts = status.request_counts
        total = getattr(counts, "total", 0) or 0
        completed = getattr(counts, "completed", 0) or 0
        failed = getattr(counts, "failed", 0) or 0

        progress = f"完成 {completed}/{total}" if total else "排队中"
        sys.stdout.write(
            f"\r  ⏳ 任务状态: {st} | {progress} | 失败: {failed} "
            f"| 用时: {elapsed}s  "
        )
        sys.stdout.flush()

        if st == "completed":
            sys.stdout.write("\n")
            sys.stdout.flush()
            logger.info("Batch 任务完成!")

            chk["status"] = "completed"
            chk["updated_at"] = now_iso()
            _save_batch_checkpoint(chk)

            _download_and_parse_batch(client, status, chk)
            return
        elif st in ("failed", "expired", "cancelled"):
            sys.stdout.write("\n")
            sys.stdout.flush()
            logger.error(f"Batch 任务失败（status={st}），降级为同步模式")
            _clear_batch_checkpoint()
            _run_sync_validation_for_pmids(chk.get("pmid_list", []))
            return

        time.sleep(LLM_BATCH_POLL_INTERVAL)


def _download_and_parse_batch(client, batch_status, chk: dict):
    """下载 Batch 结果并解析写入 DB，处理 error_file 降级"""
    all_pmids = chk.get("pmid_list", [])

    success_total = 0
    error_pmids = []

    output_id = getattr(batch_status, "output_file_id", None)
    error_id = getattr(batch_status, "error_file_id", None)

    if output_id:
        logger.info("下载结果文件...")
        try:
            content = client.files.content(output_id)
            tmp_path = Path(OUTPUT_DIR) / f"batch_output_{chk['batch_id']}.jsonl"
            tmp_path.parent.mkdir(parents=True, exist_ok=True)
            content.write_to_file(str(tmp_path))

            success_total, error_pmids = _parse_batch_results(str(tmp_path))
            logger.info(f"结果已写入 DB: {success_total} 篇成功, {len(error_pmids)} 篇解析失败")
        except Exception as e:
            logger.error(f"下载/解析输出文件失败: {e}")
            _clear_batch_checkpoint()
            _run_sync_validation_for_pmids(all_pmids)
            return

    if error_id:
        logger.info("下载错误文件...")
        try:
            error_content = client.files.content(error_id)
            error_path = Path(OUTPUT_DIR) / f"batch_errors_{chk['batch_id']}.jsonl"
            error_path.parent.mkdir(parents=True, exist_ok=True)
            error_content.write_to_file(str(error_path))

            with open(error_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                        error_pmids.append(item.get("custom_id", ""))
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            logger.warning(f"下载错误文件失败: {e}")

    # 降级：对 error 中的 PMID 执行同步验证
    if error_pmids:
        logger.info(f"Batch 错误 {len(error_pmids)} 篇，降级为同步模式重试")
        _run_sync_validation_for_pmids(error_pmids)


def _finalize_batch(chk: dict):
    """收尾：清除检查点、输出统计、导出 CSV"""
    _clear_batch_checkpoint()

    with get_conn(DB_PATH) as conn:
        verdicts = conn.execute("""
            SELECT llm_verdict, COUNT(*) as cnt
            FROM llm_validation
            GROUP BY llm_verdict
        """).fetchall()

    logger.info("LLM 验证统计（Batch）:")
    log_info = []
    total = 0
    for v, cnt in verdicts:
        total += cnt
        log_info.append((v, cnt))
    logger.info("LLM 验证统计（Batch）:")
    for v, cnt in log_info:
        logger.info(f"  {v}: {cnt} 篇 ({cnt / total * 100:.1f}%)")

    csv_path_file = _export_review_csv()
    logger.info(f"LLM 验证完成，待复核清单: {csv_path_file}")


def import_human_review(csv_path: str | None = None):
    """
    导入人工复核结果 CSV，更新审核意见并导出最终过滤结果。
    未指定路径时自动使用最新的 llm_review_pending_*.csv。
    """
    out_dir = Path(OUTPUT_DIR)
    if csv_path:
        review_file = Path(csv_path)
    else:
        candidates = sorted(out_dir.glob("llm_review_pending_*.csv"), reverse=True)
        if not candidates:
            logger.error("未找到 llm_review_pending_*.csv 文件")
            return
        review_file = candidates[0]

    logger.info(f"导入复核文件: {review_file}")

    with open(review_file, "r", encoding="utf-8-sig", newline="") as f_check:
        reader_check = csv.DictReader(f_check)
        required_cols = {"pmid", "human_review"}
        if not required_cols.issubset(reader_check.fieldnames or []):
            missing = required_cols - set(reader_check.fieldnames or [])
            logger.error(f"CSV 缺少必需列: {missing}")
            return

    passed = 0
    rejected = 0
    skipped = 0
    updates = []

    with open(review_file, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pmid = row.get("pmid", "").strip()
            review = row.get("human_review", "").strip().upper()
            if not pmid:
                skipped += 1
                continue
            if review not in ("Y", "N"):
                skipped += 1
                continue
            updates.append((review, pmid))
            if review == "Y":
                passed += 1
            else:
                rejected += 1

    if updates:
        with get_conn(DB_PATH) as conn:
            conn.executemany(UPDATE_HUMAN_REVIEW_SQL, updates)

    logger.info(f"导入完成: Y {passed} 篇, N {rejected} 篇, 跳过 {skipped} 行")

    filtered_csv = _export_filtered_csv()
    if filtered_csv:
        logger.info(f"最终过滤结果已导出: {filtered_csv}")

    return {"passed": passed, "rejected": rejected, "skipped": skipped}


def _export_filtered_csv() -> Path | None:
    """导出 LLM 验证 + 人工复核后的最终结果"""
    with get_conn(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT a.pmid, a.title, a.abstract, a.keywords, a.mesh_terms,
                   a.pub_year, a.journal, a.doi, a.pmc_id,
                   a.article_types, a.authors, a.affiliation, a.language,
                   v.llm_verdict, v.reason, v.human_review
            FROM articles a
            JOIN llm_validation v ON a.pmid = v.pmid
            WHERE v.human_review = 'Y'
               OR (v.human_review IS NULL AND v.llm_verdict = 'RELEVANT')
        """).fetchall()

    if not rows:
        logger.info("没有符合条件的最终结果")
        return None

    out_dir = Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"llm_filtered_{ts}.csv"

    fieldnames = [
        "pmid", "title", "abstract", "keywords", "mesh_terms",
        "pub_year", "journal", "doi", "pmc_id",
        "article_types", "authors", "affiliation", "language",
        "llm_verdict", "llm_reason", "human_review",
    ]

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            d = dict(row)
            d["llm_reason"] = d.pop("reason", "")
            writer.writerow(d)

    logger.info(f"最终过滤结果: {csv_path} ({len(rows)} 篇)")
    return csv_path


RAW_EXPORT_FIELDS = [
    "pmid", "title", "abstract", "keywords", "mesh_terms",
    "pub_year", "pub_month", "journal", "journal_abbr", "doi",
    "pmc_id", "article_types", "authors", "affiliation",
    "language",
]


def _export_raw_csv(db_path: Path = DB_PATH) -> Path | None:
    """
    导出复核通过文献的原始信息 CSV（articles 表原始字段，不含 raw_xml_file、LLM/复核列）。
    筛选条件与 _export_filtered_csv 一致：
    human_review='Y' 或（未复核且 llm_verdict='RELEVANT'）。
    """
    with get_conn(db_path) as conn:
        rows = conn.execute("""
            SELECT a.pmid, a.title, a.abstract, a.keywords, a.mesh_terms,
                   a.pub_year, a.pub_month, a.journal, a.journal_abbr, a.doi,
                   a.pmc_id, a.article_types, a.authors, a.affiliation,
                   a.language
            FROM articles a
            JOIN llm_validation v ON a.pmid = v.pmid
            WHERE v.human_review = 'Y'
               OR (v.human_review IS NULL AND v.llm_verdict = 'RELEVANT')
        """).fetchall()

    if not rows:
        logger.info("没有符合条件的原始文献记录")
        return None

    out_dir = Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"articles_raw_{ts}.csv"

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=RAW_EXPORT_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))

    logger.info(f"原始文献信息已导出: {csv_path} ({len(rows)} 篇)")
    return csv_path


ARTICLES_EXPORT_FIELDS = [
    "pmid", "title", "abstract", "keywords", "mesh_terms",
    "authors", "year", "journal", "doi",
]

EXPORTED_PMIDS_FILENAME = "exported_pmids.txt"


def _load_exported_pmids() -> set[str]:
    """读取已导出的 PMID 集合"""
    path = Path(OUTPUT_DIR) / EXPORTED_PMIDS_FILENAME
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def _append_exported_pmids(pmids: list[str]) -> None:
    """追写本次导出的 PMID 到记录文件"""
    path = Path(OUTPUT_DIR) / EXPORTED_PMIDS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for p in sorted(pmids):
            f.write(p + "\n")


def _export_articles_csv(db_path: Path = DB_PATH) -> Path | None:
    """
    导出主项目（cellspatio-agent）兼容 CSV，供 KnowledgeImporter.import_from_csv 直接导入。
    字段对齐 _convert_article_to_text 期望：year（由 pub_year 映射）、
    数组字段 | 分隔 → 逗号分隔，仅含纯文献信息 9 列。
    筛选条件与 _export_raw_csv 一致；增量：跳过 exported_pmids.txt 已记录的 PMID。
    """
    exported = _load_exported_pmids()
    with get_conn(db_path) as conn:
        rows = conn.execute("""
            SELECT a.pmid, a.title, a.abstract, a.keywords, a.mesh_terms,
                   a.pub_year, a.journal, a.doi, a.authors
            FROM articles a
            JOIN llm_validation v ON a.pmid = v.pmid
            WHERE (v.human_review = 'Y'
               OR (v.human_review IS NULL AND v.llm_verdict = 'RELEVANT'))
              AND a.pmid NOT IN ({})
        """.format(",".join("?" * len(exported))), tuple(sorted(exported))).fetchall()

    rows = [dict(r) for r in rows]
    if not rows:
        logger.info("没有新的符合条件的文献记录，跳过导出")
        return None

    for r in rows:
        r["year"] = r.pop("pub_year")
        for col in ("keywords", "mesh_terms", "authors"):
            if isinstance(r.get(col), str):
                r[col] = r[col].replace("|", ",")

    out_dir = Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"articles_{ts}.csv"

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=ARTICLES_EXPORT_FIELDS)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    _append_exported_pmids([r["pmid"] for r in rows])
    logger.info(f"主项目兼容文献已导出: {csv_path} ({len(rows)} 篇)")
    return csv_path
