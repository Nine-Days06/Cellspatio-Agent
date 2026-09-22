"""数据获取可审计 lineage（JSONL 追加）"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def append_lineage(path: str | Path, record: dict[str, Any]) -> None:
    """追加一条 lineage 记录，自动写入 UTC 时间戳 ts"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = dict(record)
    row.setdefault("ts", datetime.now(timezone.utc).isoformat())
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_lineage(path: str | Path) -> list[dict[str, Any]]:
    """读取全部 lineage；文件不存在返回空列表"""
    p = Path(path)
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows
