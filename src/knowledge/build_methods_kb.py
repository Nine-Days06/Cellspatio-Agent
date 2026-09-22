"""方法学知识库建库：python -m src.knowledge.build_methods_kb"""
import argparse
import logging

from src.knowledge.lightrag_client import LightRAGClient
from src.knowledge.methods_kb import MethodsKb

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def build_methods_kb(kb: MethodsKb, cards_dir=None) -> int:
    """从卡片目录摄入方法库，返回插入条数"""
    n = kb.build_from_cards(cards_dir)
    logger.info("方法卡片插入完成: %s 条", n)
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description="构建方法学知识库")
    parser.add_argument(
        "--working-dir",
        default="knowledge_base_methods",
        help="方法库 LightRAG 工作目录（与主库隔离）",
    )
    parser.add_argument(
        "--cards-dir",
        default="knowledge_base_methods_cards",
        help="方法卡片 Markdown 目录",
    )
    args = parser.parse_args()

    client = LightRAGClient(working_dir=args.working_dir)
    kb = MethodsKb(client=client, cards_dir=args.cards_dir)
    n = build_methods_kb(kb, cards_dir=args.cards_dir)
    print(f"inserted={n}")


if __name__ == "__main__":
    main()
