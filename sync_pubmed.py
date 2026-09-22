"""pubmed-etl 导出 → 主项目知识库导入 一键同步脚本

用法：
    python sync_pubmed.py                 # 导出 + 导入（默认）
    python sync_pubmed.py --no-import     # 只导出不导入
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


def import_via_cli(import_dir: Path) -> dict:
    """通过 import_cli 模块导入 data/import 目录"""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from src.knowledge.import_cli import import_from_directory

    return import_from_directory(str(import_dir))


def sync_and_import(etl_dir: Path, import_dir: Path, do_import: bool = True) -> dict:
    """核心同步逻辑：导出 → 复制 → 导入，返回统计"""
    csv_path = run_etl_export(etl_dir)
    if csv_path is None:
        return {"exported": 0, "imported": 0, "failed": 0, "csv": None}

    dest = copy_to_import(csv_path, import_dir)
    result = {"exported": 1, "imported": None, "failed": 0, "csv": str(dest)}
    if do_import:
        import_result = import_via_cli(import_dir)
        result["imported"] = import_result.get("total_count", 0)
        result["failed"] = 0 if import_result.get("success") else 1
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="pubmed-etl 导出并导入主项目知识库")
    parser.add_argument("--etl-dir", default=str(REPO_ROOT / "pubmed-etl"))
    parser.add_argument("--import-dir", default=str(REPO_ROOT / "data" / "import"))
    parser.add_argument("--no-import", action="store_true", help="只导出不导入")
    args = parser.parse_args(argv)

    result = sync_and_import(
        etl_dir=Path(args.etl_dir),
        import_dir=Path(args.import_dir),
        do_import=not args.no_import,
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
