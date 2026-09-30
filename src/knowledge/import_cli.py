"""知识库导入 CLI 入口 - 提供命令行接口导入文献数据"""
import argparse

from src.config import LLM_PROVIDER
from src.knowledge.knowledge_importer import KnowledgeImporter


def build_importer(provider: str | None = None) -> KnowledgeImporter:
    """构建知识导入器

    Args:
        provider: LLM 供应商名称，None 时使用默认值 src.config.LLM_PROVIDER

    Returns:
        KnowledgeImporter 实例
    """
    from src.knowledge.lightrag_client import LightRAGClient

    working_dir = "./knowledge_base"
    lightrag_client = LightRAGClient(
        working_dir=working_dir,
        config={"provider": provider}
    )

    importer = KnowledgeImporter(lightrag_client)
    return importer


def import_from_directory(
    dir_path: str,
    provider: str | None = None,
    *,
    files: list[str] | None = None,
    reimport_all: bool = False,
) -> dict:
    """从目录增量导入文献数据（受 data/import/import_ledger.json 台账管控）

    Args:
        dir_path: 目录路径
        provider: LLM 供应商名称，None 时使用默认值
        files: 只导入这些文件名（相对 dir_path），None 表示扫描整个目录
        reimport_all: True 时忽略台账过滤，全量重导（有意的全量刷新）

    Returns:
        导入结果字典：success / total_count（提交总数）/ new_count（新批次文章数）/
        imported_files / skipped_files / failed_files；失败时另含 error
    """
    importer = build_importer(provider)
    result = importer.import_incremental(
        dir_path, files=files, reimport_all=reimport_all
    )
    return result


def main(argv: list[str] | None = None) -> int:
    """命令行入口

    Args:
        argv: 命令行参数列表，默认为 sys.argv[1:]

    Returns:
        退出码：0=成功，1=失败
    """
    parser = argparse.ArgumentParser(
        description="导入文献数据到 LightRAG 知识库"
    )
    parser.add_argument(
        "--dir",
        required=True,
        help="要导入的目录路径"
    )
    parser.add_argument(
        "--provider",
        default=LLM_PROVIDER,
        help=f"LLM 供应商名称（默认: {LLM_PROVIDER}）"
    )
    parser.add_argument(
        "--reimport-all",
        action="store_true",
        help="忽略台账，全量重导目录下所有文件（默认仅导入台账未记录的新文件）",
    )

    args = parser.parse_args(argv)

    print(f"开始导入文献数据到目录: {args.dir}")
    print(f"使用 LLM 供应商: {args.provider}")

    result = import_from_directory(
        args.dir, args.provider, reimport_all=args.reimport_all
    )

    if result.get("success"):
        print(
            f"导入成功！提交 {result.get('total_count', 0)} 篇文献"
            f"（其中新批次 {result.get('new_count', 0)} 篇）"
        )
        print(
            f"导入 {len(result.get('imported_files', []))} 个文件，"
            f"跳过 {len(result.get('skipped_files', []))} 个已导入文件"
        )
        return 0
    else:
        print(f"导入失败: {result.get('error', '未知错误')}")
        return 1
