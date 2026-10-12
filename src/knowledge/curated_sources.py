"""非文献 curated 数据源：CellMarker / Cell Ontology 的下载缓存与文本化

- ensure_file：URL → 本地缓存文件（已存在跳过下载）
- _pack_entries：条目贪心装箱为定长文档
- cellmarker_texts / cell_ontology_texts：源文件 → 入库文档列表
"""
import re
from pathlib import Path

import openpyxl

from src.data.download import ensure_file
from src.data.gene_map import GeneIndex, gene_curie, get_gene_index

CELLMARKER_URL = (
    "http://117.50.127.228/CellMarker/CellMarker_download_files/file/"
    "Cell_marker_Human.xlsx"
)

# 本地缓存位置（data/raw 已 gitignore；源文件已预下载，ensure_file 命中即跳过）
ROOT = Path(__file__).resolve().parents[2]
CELLMARKER_PATH = ROOT / "data" / "raw" / "cellmarker" / "Cell_marker_Human.xlsx"

# 文档头：必须与 rebuild_kb.CURATED_HEADERS 一致，否则清库守卫会误判为文献
CELLMARKER_HEADER = "# CellMarker 细胞类型:"
CL_OBO_URL = "http://purl.obolibrary.org/obo/cl.obo"
CL_OBO_PATH = ROOT / "data" / "raw" / "cellontology" / "cl.obo"
CL_HEADER = "# Cell Ontology 术语:"


def _pack_entries(entries: list[str], header: str, max_chars: int = 3000) -> list[str]:
    """条目贪心装箱为文档：按序累积，超预算封箱；单条超预算独占一箱

    max_chars 含 header；正文预算 = max_chars - len(header) - 1（换行符）。
    """
    budget = max(1, max_chars - len(header) - 1)
    docs: list[str] = []
    buf: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal buf, size
        if buf:
            docs.append(header + "\n" + "\n".join(buf))
            buf = []
            size = 0

    for entry in entries:
        entry_len = len(entry)
        cost = entry_len + (1 if buf else 0)  # 箱内换行符
        if buf and size + cost > budget:
            flush()
            cost = entry_len
        if not buf and entry_len > budget:
            docs.append(header + "\n" + entry)  # 超长条目独占，保条目完整
            continue
        buf.append(entry)
        size += cost
    flush()
    return docs


def _cell_str(value: object) -> str:
    """xlsx 单元格值归一为字符串：None → ""，整数浮点 123.0 → "123"（PMID 列实测为数值）"""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _normalize_cl(value: str) -> str:
    """CellMarker 写法 CL_0000235 归一为本体格式 CL:0000235"""
    return f"CL:{value[3:]}" if value.upper().startswith("CL_") else value


def _markers_with_ids(markers: list[str], index: GeneIndex) -> str:
    """标志物渲染：命中映射表才加 (NCBIGene:NNN)，未命中保持裸符号"""
    parts = []
    for marker in markers:
        gene_id = index.lookup(symbol=marker)
        parts.append(f"{marker} ({gene_curie(gene_id)})" if gene_id else marker)
    return ", ".join(parts)


def cellmarker_texts(xlsx_path: Path | None = None, gene_index: GeneIndex | None = None) -> list[str]:
    """CellMarker Human xlsx → 入库文档列表

    gene_index: 基因映射索引；None 时用进程单例（首次会联网下载 gene_info）

    - 按 cell_name 聚合（实测 60,877 行 → 1,713 个细胞类型）；标志物不设上限
    - 组织去重后取前 20；PMID 去重后渲染前 10，共 N 篇计去重总数
    - 条目格式：## 细胞名 / Cell Ontology ID / 组织 / 标志物 / 文献（缺字段省略行）
    """
    path = xlsx_path if xlsx_path is not None else ensure_file(CELLMARKER_URL, CELLMARKER_PATH)
    index = gene_index or get_gene_index()
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        header = [str(c or "").strip().lower() for c in next(rows)]
        idx = {name: i for i, name in enumerate(header)}
        need = ("cell_name", "cellontology_id", "tissue_type", "marker", "pmid")
        missing = [c for c in need if c not in idx]
        if missing:
            raise ValueError(f"CellMarker 表缺少列: {', '.join(missing)}")
        order: list[str] = []
        slots: dict[str, dict[str, list[str]]] = {}
        for row in rows:
            cell_name = _cell_str(row[idx["cell_name"]])
            if not cell_name:
                continue
            if cell_name not in slots:
                slots[cell_name] = {"cl": [], "tissues": [], "markers": [], "pmids": []}
                order.append(cell_name)
            slot = slots[cell_name]
            cl = _normalize_cl(_cell_str(row[idx["cellontology_id"]]))
            if cl and cl not in slot["cl"]:
                slot["cl"].append(cl)
            tissue = _cell_str(row[idx["tissue_type"]])
            if tissue and tissue not in slot["tissues"]:
                slot["tissues"].append(tissue)
            marker = _cell_str(row[idx["marker"]])
            if marker and marker not in slot["markers"]:
                slot["markers"].append(marker)
            pmid = _cell_str(row[idx["pmid"]])
            if pmid and pmid not in slot["pmids"]:
                slot["pmids"].append(pmid)
    finally:
        wb.close()

    entries = []
    for cell_name in order:
        slot = slots[cell_name]
        lines = [f"## {cell_name}"]
        if slot["cl"]:
            lines.append(f"Cell Ontology ID: {', '.join(slot['cl'])}")
        if slot["tissues"]:
            lines.append(f"组织：{', '.join(slot['tissues'][:20])}")
        if slot["markers"]:
            lines.append(f"标志物：{_markers_with_ids(slot['markers'], index)}")
        if slot["pmids"]:
            shown = slot["pmids"][:10]
            lines.append(f"文献：PMID {', '.join(shown)}（共 {len(slot['pmids'])} 篇）")
        entries.append("\n".join(lines))
    return _pack_entries(entries, CELLMARKER_HEADER)


def cell_ontology_texts(obo_path: Path | None = None) -> list[str]:
    """cl.obo → 入库文档列表（仅未废弃的 CL: 术语）

    实测 18,914 stanza → 3,532 个 CL id → 3,335 个未废弃。
    条目格式（" | " 连接）：CL:xxx 名称 | is_a: ... | relationship: ... | comment: ...
    is_a 去掉 " ! 注释"；relationship 仅对象 token 以 CL: 开头才保留（原文不剥离注释）。
    """
    path = obo_path if obo_path is not None else ensure_file(CL_OBO_URL, CL_OBO_PATH)
    text = path.read_text(encoding="utf-8")
    entries: list[str] = []
    for stanza in re.split(r"^\[(?:Term|Typedef)\]$", text, flags=re.MULTILINE)[1:]:
        kv: dict[str, list[str]] = {}
        current = ""
        for line in stanza.splitlines():
            if line.startswith("\t"):
                # OBO 续行（tab 缩进）并入上一个 key 的末值
                if current and kv.get(current):
                    kv[current][-1] += " " + line.strip()
                continue
            if not line.strip() or line.startswith("!"):
                continue
            key, sep, value = line.partition(": ")
            if not sep:
                continue
            current = key
            kv.setdefault(key, []).append(value)
        ids = kv.get("id", [])
        if not ids or not ids[0].startswith("CL:"):
            continue
        if kv.get("is_obsolete"):
            continue
        name = kv.get("name", [""])[0]
        parts = [f"{ids[0]} {name}"]
        for is_a in kv.get("is_a", []):
            parts.append(f"is_a: {is_a.replace(' ! ', ' ')}")
        for rel in kv.get("relationship", []):
            if any(tok.startswith("CL:") for tok in rel.split()):
                parts.append(f"relationship: {rel}")
        for comment in kv.get("comment", []):
            parts.append(f"comment: {comment}")
        entries.append(" | ".join(parts))
    return _pack_entries(entries, CL_HEADER)
