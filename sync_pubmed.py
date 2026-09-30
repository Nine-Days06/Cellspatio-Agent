"""pubmed-etl 导出 → 主项目知识库导入 一键同步脚本

用法：
    python sync_pubmed.py                 # 导出 + 增量导入本次新 CSV（默认）
    python sync_pubmed.py --no-import     # 只导出不导入
    python sync_pubmed.py --reimport-all  # 忽略台账，全量重导 data/import/ 下所有文件

导入侧为增量：每次只导入本次复制的那一个 CSV，data/import/import_ledger.json
记录已导入批次，重复运行为空操作；删除该台账文件即触发一次有意的全量重导。
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent


def run_etl_export(etl_dir: Path, python: str | None = None) -> Path | None:
    """调用 pubmed-etl --step export，返回最新导出的主项目兼容 CSV（无新数据则 None）"""
    python = python or sys.executable
    subprocess.run(
        [python, str(etl_dir / "main.py"), "--step", "export"],
        cwd=str(etl_dir),
        check=True,
    )
    output_dir = etl_dir / "data" / "output"
    if not output_dir.exists():
        return None
    csvs = sorted(output_dir.glob("articles_*.csv"))
    if not csvs:
        return None
    return csvs[-1]


def copy_to_import(csv_path: Path, import_dir: Path) -> Path:
    """复制导出的 CSV 到主项目 data/import/，返回目标路径"""
    import_dir.mkdir(parents=True, exist_ok=True)
    dest = import_dir / csv_path.name
    shutil.copy2(csv_path, dest)
    return dest


def import_via_cli(import_dir: Path, csv_name: str | None = None, reimport_all: bool = False) -> dict:
    """通过 import_cli 模块增量导入 data/import 目录

    Args:
        import_dir: 导入目录（台账位于该目录下 import_ledger.json）
        csv_name: 本次复制的文件名，只导入这一个文件；None 表示扫描整个目录
        reimport_all: True 时忽略台账过滤，全量重导目录下所有文件
    """
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from src.knowledge.import_cli import import_from_directory

    return import_from_directory(
        str(import_dir),
        files=[csv_name] if csv_name else None,
        reimport_all=reimport_all,
    )


def sync_and_import(
    etl_dir: Path,
    import_dir: Path,
    do_import: bool = True,
    reimport_all: bool = False,
) -> dict:
    """核心同步逻辑：导出 → 复制 → 增量导入，返回统计

    返回键（向后兼容）：
        exported: 本次复制到 data/import/ 的 CSV 数（0 或 1）
        imported: 本次「新批次」文章数——只统计台账未记录的 CSV 里的文章，
            即真正首次提交给 LightRAG 的数量，而非扫描数。
            注意其边界：它不等于知识库实际新增的文档数——LightRAG 1.5.7 在
            pipeline 内部按 content_hash 与文件名静默去重，且不通过 SDK 返回
            逐文档去重信号，该数字在本层不可观测，故不虚构。
        failed: 导入是否失败（0=成功，1=失败）
        csv: 本次复制到 data/import/ 的文件路径（无新数据时为 None）
    """
    csv_path = run_etl_export(etl_dir)
    if csv_path is None:
        return {"exported": 0, "imported": 0, "failed": 0, "csv": None}

    dest = copy_to_import(csv_path, import_dir)
    result = {"exported": 1, "imported": None, "failed": 0, "csv": str(dest)}
    if do_import:
        import_result = import_via_cli(
            import_dir,
            csv_name=None if reimport_all else dest.name,
            reimport_all=reimport_all,
        )
        result["imported"] = import_result.get("new_count", 0)
        result["failed"] = 0 if import_result.get("success") else 1
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="pubmed-etl 导出并导入主项目知识库")
    parser.add_argument("--etl-dir", default=str(REPO_ROOT / "pubmed-etl"))
    parser.add_argument("--import-dir", default=str(REPO_ROOT / "data" / "import"))
    parser.add_argument("--no-import", action="store_true", help="只导出不导入")
    parser.add_argument(
        "--reimport-all",
        action="store_true",
        help="忽略台账，全量重导 data/import 下所有文件（默认只导入本次新 CSV）",
    )
    args = parser.parse_args(argv)

    result = sync_and_import(
        etl_dir=Path(args.etl_dir),
        import_dir=Path(args.import_dir),
        do_import=not args.no_import,
        reimport_all=args.reimport_all,
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
