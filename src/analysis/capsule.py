"""可复现分析胶囊：导出问题/意图/脚本/结果元数据"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def export_analysis_capsule(
    question: str,
    intent: dict[str, Any],
    params: dict[str, Any],
    script_code: str,
    results: dict[str, Any],
    out_root: str | Path = "output/capsules",
) -> Path:
    """写入 `<out_root>/<timestamp>/` 并返回该目录路径"""
    root = Path(out_root)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    capsule = root / ts
    capsule.mkdir(parents=True, exist_ok=True)

    (capsule / "question.txt").write_text(question or "", encoding="utf-8")
    (capsule / "script.R").write_text(script_code or "", encoding="utf-8")
    (capsule / "intent.json").write_text(
        json.dumps(intent, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (capsule / "meta.json").write_text(
        json.dumps(
            {
                "params": params,
                "results": results,
                "exported_at": ts,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return capsule
