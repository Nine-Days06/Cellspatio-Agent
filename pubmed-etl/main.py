"""PubMed ETL 主程序 - 支持分步执行"""
import argparse
import sys
from pathlib import Path

# 确保项目根目录在 Python 路径中
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import (
    DB_PATH, RAW_XML_DIR, OUTPUT_DIR, LOG_DIR,
    PUBMED_QUERY,
)
from utils.db import init_db


def step_download(query: str = None):
    """下载文献"""
    from downloader.pubmed_downloader import run_download
    return run_download(query or PUBMED_QUERY)


def step_parse(xml_dir: Path = None):
    """解析 XML"""
    from parser.xml_parser import run_parse
    run_parse(xml_dir=xml_dir or RAW_XML_DIR, db_path=DB_PATH)


def step_clean():
    """硬过滤"""
    from cleaner.hard_filter import run_hard_filter
    return run_hard_filter(db_path=DB_PATH)


def step_pdf():
    """下载 OA 全文"""
    from downloader.pdf_downloader import run_pdf_download
    run_pdf_download(db_path=DB_PATH)


def step_pdf_retry():
    """重试失败的 PDF 下载"""
    from downloader.pdf_downloader import run_pdf_retry
    run_pdf_retry(db_path=DB_PATH)


def step_validate(batch=False):
    """LLM 验证"""
    from cleaner.llm_validator import run_validation
    run_validation(batch_mode=batch)


def step_export():
    """导出"""
    from cleaner.llm_validator import _export_raw_csv
    from utils.logger import get_logger
    
    logger = get_logger("main")
    path = _export_raw_csv()
    if path:
        logger.info(f"原始文献信息已导出: {path}")


def step_import_review(csv_path: str = None):
    """导入人工复核结果"""
    from cleaner.llm_validator import import_human_review
    import_human_review(csv_path)


def main():
    parser = argparse.ArgumentParser(
        description="人类多组学 PubMed 文献处理工具"
    )
    parser.add_argument(
        "--step",
        choices=["download", "parse", "clean", "pdf", "pdf-retry", "validate", "import-review", "export", "all"],
        default="all",
        help="运行指定阶段（默认 all）",
    )
    parser.add_argument(
        "--csv",
        default=None,
        help="import-review 阶段的 CSV 文件路径（默认自动查找最新的复核文件）",
    )
    parser.add_argument(
        "--query",
        default=None,
        help="自定义 PubMed 搜索词（仅在 download / all 阶段生效）",
    )
    parser.add_argument(
        "--xml-dir",
        default=None,
        help="XML 文件目录（仅在 parse / all 阶段生效）",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="使用 Batch API 进行 LLM 验证",
    )
    args = parser.parse_args()

    # 初始化目录和数据库
    for d in [RAW_XML_DIR, OUTPUT_DIR, LOG_DIR, DB_PATH.parent]:
        d.mkdir(parents=True, exist_ok=True)
    init_db(DB_PATH)

    print("▶  人类多组学文献处理系统启动")
    print(f"   运行阶段: {args.step}")
    print(f"   数据库:   {DB_PATH}")

    step = args.step

    if step in ("download", "all"):
        step_download(args.query)

    if step in ("parse", "all"):
        step_parse(Path(args.xml_dir) if args.xml_dir else None)

    if step in ("clean", "all"):
        step_clean()

    if step == "pdf":
        step_pdf()

    if step == "pdf-retry":
        step_pdf_retry()

    if step == "validate":
        step_validate(args.batch)

    if step == "import-review":
        step_import_review(args.csv)

    if step in ("export", "all"):
        step_export()

    print("✔  全部流程完成")
    print(f"   输出目录: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
