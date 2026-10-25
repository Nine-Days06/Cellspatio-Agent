"""固定 QC 的本地阈值预览；不执行过滤，也不向模型提供逐细胞数据。"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from src.analysis.sc_qc import QC_TOOL_VERSION, _fingerprints, _read_evidence, qc_script
from src.data.tenx import tenx_files

PREVIEW_TOOL_VERSION = "1.0.0"
THRESHOLD_SCHEMA = "qc-thresholds/1.0.0-and-inclusive"
QC_FILES = (
    "metadata.json",
    "cell_qc.csv",
    "features.csv",
    "script.R",
    "summary.json",
    "versions.txt",
    "qc.png",
    "stdout.txt",
    "stderr.txt",
)
NonnegativeInt = Annotated[StrictInt, Field(ge=0)]


class Thresholds(BaseModel):
    """无默认科学阈值；AND 闭区间，整数不可由 bool/字符串隐式转换。"""

    model_config = ConfigDict(extra="forbid")
    n_feature_min: NonnegativeInt
    n_feature_max: NonnegativeInt
    percent_mt_max: Annotated[Decimal, Field(ge=0, le=100, allow_inf_nan=False)]
    n_count_min: NonnegativeInt | None = None
    n_count_max: NonnegativeInt | None = None

    @field_validator("percent_mt_max", mode="before")
    @classmethod
    def numeric_percent(cls, value: Any) -> Any:
        """拒绝 JSON 字符串和布尔值，保留十进制比较语义。"""
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise ValueError("线粒体阈值必须是数值")
        return value

    @model_validator(mode="after")
    def ordered(self) -> Thresholds:
        """上下限不能倒置。"""
        if self.n_feature_min > self.n_feature_max or (
            self.n_count_min is not None
            and self.n_count_max is not None
            and self.n_count_min > self.n_count_max
        ):
            raise ValueError("下限不能超过上限")
        return self

    def canonical(self) -> dict[str, Any]:
        """不依赖 JSON 键顺序或浮点字符串表示绑定参数。"""
        result = self.model_dump()
        result["percent_mt_max"] = (
            format(self.percent_mt_max, "f").rstrip("0").rstrip(".")
            if self.percent_mt_max % 1
            else str(int(self.percent_mt_max))
        )
        return result


def _digest(data: bytes) -> str:
    """同一份实际读取的字节用于身份校验和解析。"""
    return hashlib.sha256(data).hexdigest()


def _identity(data: bytes) -> dict[str, Any]:
    """文件内容身份。"""
    return {"bytes": len(data), "sha256": _digest(data)}


def _qc_bytes(directory: Path) -> dict[str, bytes]:
    """只读取受控目录中的固定文件，不接受外部产物路径。"""
    files = {}
    for name in QC_FILES:
        path = directory / name
        if path.resolve().parent != directory or not path.is_file():
            raise ValueError("QC 证据路径非法或缺失，请重新 QC")
        files[name] = path.read_bytes()
    return files


def _input_identity(path: Path) -> list[dict[str, Any]]:
    """复核实际输入文件集合和内容，不重新解析完整 MTX。"""
    paths = list(tenx_files(path).values()) if path.is_dir() else [path]
    return sorted(_fingerprints(paths), key=lambda record: record["path"])


def register_evidence(directory: Path, root: Path) -> dict[str, Any]:
    """只认领固定 QC 的成功结果，旧聊天消息不会自动认领。"""
    directory, root = directory.resolve(), root.resolve()
    if directory.parent != root or directory == root:
        raise ValueError("QC 目录不在受控根目录")
    files = _qc_bytes(directory)
    meta = json.loads(files["metadata.json"])
    if (
        meta.get("tool") != "sc_qc"
        or meta.get("tool_version") != QC_TOOL_VERSION
        or meta.get("status") != "needs_confirmation"
        or meta.get("returncode") != 0
        or meta.get("stage") != "qc_review"
        or meta.get("scientific_status") != "unreviewed"
    ):
        raise ValueError("QC 未成功或工具版本不符")
    path = Path(meta["input_path"]).resolve()
    inputs = _input_identity(path)
    if inputs != sorted(meta["inputs_before"], key=lambda r: r["path"]) or inputs != sorted(
        meta["inputs_after"], key=lambda r: r["path"]
    ):
        raise ValueError("QC 原输入已变化，请重新 QC")
    if files["script.R"].decode("utf-8").replace("\r\n", "\n") != qc_script(str(path), directory):
        raise ValueError("QC 固定脚本不符")
    summary = _read_evidence(directory, path)
    if json.loads(files["summary.json"]) != summary or _qc_bytes(directory) != files:
        raise ValueError("QC 证据不一致，请重新 QC")
    return {
        "inputs": inputs,
        "artifacts": {name: _identity(data) for name, data in files.items()},
        "tool_version": QC_TOOL_VERSION,
        "input_path": str(path),
        "summary": summary,
    }


def checked_evidence(run: dict[str, Any], root: Path) -> dict[str, bytes]:
    """预览和批准均复核同一份注册证据与原输入。"""
    directory = Path(run["directory"]).resolve()
    if directory.parent != root.resolve():
        raise ValueError("QC 目录非法，请重新 QC")
    evidence = run["evidence"]
    if evidence["tool_version"] != QC_TOOL_VERSION:
        raise ValueError("QC 工具已变化，请重新 QC")
    files = _qc_bytes(directory)
    if {name: _identity(data) for name, data in files.items()} != evidence[
        "artifacts"
    ] or _input_identity(Path(evidence["input_path"])) != evidence["inputs"]:
        raise ValueError("原输入或 QC 证据已变化，请重新 QC")
    return files


def decisions(data: bytes, thresholds: Thresholds) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """逐 barcode 记录全部重叠原因；NA/零 counts 阻断批准。"""
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    if reader.fieldnames != [
        "barcode",
        "nCount_RNA",
        "nFeature_RNA",
        "percent_mt",
        "missing_reason",
    ]:
        raise ValueError("QC 表头不符")
    rows, seen, reasons, blocked = [], set(), {}, []
    for row in reader:
        if (
            None in row
            or any(value is None for value in row.values())
            or not row["barcode"]
            or row["barcode"] in seen
        ):
            raise ValueError("QC 行非法或 barcode 重复")
        seen.add(row["barcode"])
        try:
            count, feature = Decimal(row["nCount_RNA"]), Decimal(row["nFeature_RNA"])
            if (
                any(
                    not v.is_finite() or v < 0 or v != v.to_integral_value()
                    for v in (count, feature)
                )
                or feature > count
                or (count == 0) != (feature == 0)
            ):
                raise ValueError("QC counts/features 无效")
            hits = []
            for name, value, limit, below in (
                ("n_feature_below_min", feature, thresholds.n_feature_min, True),
                ("n_feature_above_max", feature, thresholds.n_feature_max, False),
                ("n_count_below_min", count, thresholds.n_count_min, True),
                ("n_count_above_max", count, thresholds.n_count_max, False),
            ):
                if limit is not None and (value < limit if below else value > limit):
                    hits.append(name)
            if row["percent_mt"] == "NA":
                if not row["missing_reason"]:
                    raise ValueError("NA 缺少原因")
                if "percent.mt 不可定义，不能批准；请修正输入并重新 QC" not in blocked:
                    blocked.append("percent.mt 不可定义，不能批准；请修正输入并重新 QC")
                hits.append("mt_unavailable")
            else:
                percent = Decimal(row["percent_mt"])
                if (
                    not percent.is_finite()
                    or not 0 <= percent <= 100
                    or row["missing_reason"]
                    or count == 0
                ):
                    raise ValueError("QC 线粒体比例无效")
                if percent > thresholds.percent_mt_max:
                    hits.append("mt_above_max")
        except InvalidOperation as exc:
            raise ValueError("QC 指标不是合法十进制数") from exc
        for name in hits:
            reasons[name] = reasons.get(name, 0) + 1
        rows.append({"barcode": row["barcode"], "keep": not hits, "reasons": hits})
    kept = sum(row["keep"] for row in rows)
    if not rows:
        raise ValueError("QC 表为空")
    if not kept:
        blocked.append("预计保留数为零，不能批准")
    return rows, {
        "total": len(rows),
        "kept": kept,
        "excluded": len(rows) - kept,
        "excluded_percent": (len(rows) - kept) * 100 / len(rows),
        "reason_counts": reasons,
        "blocked": blocked,
        "filtered": False,
    }


def build_preview(run: dict[str, Any], root: Path, thresholds: Thresholds) -> dict[str, Any]:
    """创建独立预览目录；不覆写 QC 或旧预览。"""
    files = checked_evidence(run, root)
    rows, summary = decisions(files["cell_qc.csv"], thresholds)
    if summary["total"] != run["evidence"]["summary"]["cells"]:
        raise ValueError("QC barcode 数不符")
    binding = {
        "evidence": run["evidence"],
        "params": thresholds.canonical(),
        "schema": THRESHOLD_SCHEMA,
        "tool_version": PREVIEW_TOOL_VERSION,
    }
    preview = {
        "preview_id": uuid.uuid4().hex,
        "params": binding["params"],
        "summary": summary,
        "binding_sha256": _digest(json.dumps(binding, sort_keys=True, ensure_ascii=False).encode()),
        "schema": THRESHOLD_SCHEMA,
        "tool_version": PREVIEW_TOOL_VERSION,
    }
    directory = Path(run["directory"]) / "previews" / preview["preview_id"]
    if (
        directory.parent.resolve().parent != Path(run["directory"]).resolve()
        or directory.resolve().parent != directory.parent.resolve()
    ):
        raise ValueError("预览目录非法")
    directory.mkdir(parents=True, exist_ok=False)
    with (directory / "selection.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["barcode", "keep", "reasons"])
        writer.writerows((r["barcode"], int(r["keep"]), ";".join(r["reasons"])) for r in rows)
    (directory / "preview.json").write_text(
        json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    preview["artifacts"] = {
        name: _identity((directory / name).read_bytes())
        for name in ("selection.csv", "preview.json")
    }
    return preview


def check_preview(run: dict[str, Any], root: Path, preview: dict[str, Any]) -> None:
    """批准看到的精确预览，不接受参数或文件的后续替换。"""
    checked_evidence(run, root)
    if preview["schema"] != THRESHOLD_SCHEMA or preview["tool_version"] != PREVIEW_TOOL_VERSION:
        raise ValueError("预览工具已变化，请重新预览")
    binding = {
        "evidence": run["evidence"],
        "params": preview["params"],
        "schema": THRESHOLD_SCHEMA,
        "tool_version": PREVIEW_TOOL_VERSION,
    }
    if preview["binding_sha256"] != _digest(
        json.dumps(binding, sort_keys=True, ensure_ascii=False).encode()
    ):
        raise ValueError("预览绑定不符，请重新预览")
    directory = Path(run["directory"]) / "previews" / preview["preview_id"]
    if (
        directory.parent.resolve().parent != Path(run["directory"]).resolve()
        or directory.resolve().parent != directory.parent.resolve()
    ):
        raise ValueError("预览目录非法")
    for name, identity in preview["artifacts"].items():
        path = directory / name
        if (
            name not in ("selection.csv", "preview.json")
            or path.resolve().parent != directory.resolve()
            or _identity(path.read_bytes()) != identity
        ):
            raise ValueError("预览证据已变化，请重新预览")
    if preview["summary"]["blocked"]:
        raise ValueError("；".join(preview["summary"]["blocked"]))
