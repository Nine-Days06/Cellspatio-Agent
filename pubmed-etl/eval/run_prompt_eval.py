"""
提示词评测执行器：候选提示词 × 金标集 → 逐样本判定结果。

用法：
  cd pubmed-etl
  # 导出当前生产提示词为 baseline
  python -m eval.run_prompt_eval --export-baseline
  # 跑评测（--limit 可先小样本试跑）
  python -m eval.run_prompt_eval --prompt eval/prompts/baseline_v1.txt \
      --out data/eval_v2/runs/baseline_v1.jsonl --limit 50

约定：
- 模型固定为 .env 的 OPENAI_*（评测模型 vsllm.cc [opencode]deepseek-v4-flash），temperature=0
- system = 候选提示词全文；user = 生产同款 _build_per_article_prompt（口径一致）
- 输出 JSONL：{sample_id, layer, gold, pred, ok, reason, error}
- 断点续跑：--out 已有的 sample_id 跳过
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cleaner.llm_validator import (  # noqa: E402
    LLM_MAX_TOKENS,
    SYSTEM_PROMPT_BATCH,
    _build_per_article_prompt,
    _extract_json,
)
from openai import OpenAI  # noqa: E402

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def export_baseline() -> None:
    """把当前生产 SYSTEM_PROMPT_BATCH 落成基线提示词文件（与生产口径逐字一致）。"""
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    out = PROMPTS_DIR / "baseline_v1.txt"
    out.write_text(SYSTEM_PROMPT_BATCH, encoding="utf-8")
    print(f"基线提示词已导出: {out}（{len(SYSTEM_PROMPT_BATCH)} 字符）")


def normalize_verdict(text: str) -> str | None:
    """把模型输出归一为 RELEVANT / NOT_RELEVANT；无法识别返回 None。"""
    t = (text or "").strip().upper().replace(" ", "_")
    if t.startswith("NOT_RELEVANT"):
        return "NOT_RELEVANT"
    if t.startswith("RELEVANT"):
        return "RELEVANT"
    return None


def judge_one(client: OpenAI, system_prompt: str, rec: dict, retries: int = 3) -> dict:
    """单样本判定，返回 {sample_id, layer, gold, pred, reason, error}。"""
    user = _build_per_article_prompt(rec["pmid"], rec.get("title") or "", rec.get("abstract") or "")
    last_err = None
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model=_eval_model(),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user},
                ],
                temperature=0,
                max_tokens=LLM_MAX_TOKENS,
            )
            content = resp.choices[0].message.content or ""
            # 生产同款解析：期望 {"pmid","verdict","reason"} 单对象
            obj = None
            arr = _extract_json(content)
            if isinstance(arr, list) and arr and isinstance(arr[0], dict):
                obj = arr[0]
            else:
                try:
                    obj = json.loads(content)
                except json.JSONDecodeError:
                    pass
            verdict = None
            if isinstance(obj, dict):
                verdict = normalize_verdict(str(obj.get("verdict", "")))
            if verdict:
                return {
                    "sample_id": rec["sample_id"],
                    "layer": rec.get("layer", ""),
                    "gold": rec.get("gold", ""),
                    "pred": verdict,
                    "ok": None,
                    "reason": (obj or {}).get("reason", ""),
                    "error": "",
                }
            last_err = f"无法解析 verdict: {content[:200]}"
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
        time.sleep(2 * (attempt + 1))
    return {
        "sample_id": rec["sample_id"],
        "layer": rec.get("layer", ""),
        "gold": rec.get("gold", ""),
        "pred": "ERROR",
        "ok": None,
        "reason": "",
        "error": last_err,
    }


def _eval_model() -> str:
    import os

    return os.environ["OPENAI_MODEL"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-baseline", action="store_true")
    parser.add_argument("--gold", default="data/eval_v2/gold.jsonl")
    parser.add_argument("--samples", default="data/eval_v2/samples.jsonl")
    parser.add_argument("--prompt", default="eval/prompts/baseline_v1.txt")
    parser.add_argument("--out", required=False)
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条（试跑用）")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()

    if args.export_baseline:
        export_baseline()
        return
    if not args.out:
        parser.error("--out 未指定（除非 --export-baseline）")

    # 金标只存判定（sample_id/gold/reason），文献文本与层信息从 samples.jsonl 按 sample_id 关联
    samples = {
        r["sample_id"]: r
        for r in (
            json.loads(x)
            for x in Path(args.samples).read_text(encoding="utf-8").splitlines()
            if x.strip()
        )
    }
    records = []
    for line in Path(args.gold).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        gold = json.loads(line)
        src = samples.get(gold["sample_id"])
        if src is None:
            raise SystemExit(f"金标 sample_id 在 samples.jsonl 中不存在: {gold['sample_id']}")
        records.append({**src, "gold": gold["gold"]})
    if args.limit:
        records = records[: args.limit]
    system_prompt = Path(args.prompt).read_text(encoding="utf-8")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            done = {json.loads(x)["sample_id"] for x in f if x.strip()}
    todo = [r for r in records if r["sample_id"] not in done]
    print(f"金标 {len(records)} 条，已完成 {len(done)}，待跑 {len(todo)}")

    client = OpenAI()  # OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL 来自 .env
    with open(out_path, "a", encoding="utf-8") as fout:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futs = {pool.submit(judge_one, client, system_prompt, r): r for r in todo}
            for i, fut in enumerate(as_completed(futs), 1):
                row = fut.result()
                fout.write(json.dumps(row, ensure_ascii=False) + "\n")
                fout.flush()
                if i % 20 == 0 or i == len(todo):
                    print(f"  {i}/{len(todo)}", flush=True)
    print(f"完成 → {out_path}")


if __name__ == "__main__":
    main()
