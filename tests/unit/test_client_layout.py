"""便携包必须携带方法卡片，且从安装目录解析。"""

from pathlib import Path


def test_build_copies_method_cards() -> None:
    """构建脚本复制代码资源目录，而不是依赖用户数据目录。"""
    build = (Path(__file__).resolve().parents[2] / "client" / "build.ps1").read_text(
        encoding="utf-8"
    )
    assert "knowledge_base_methods_cards" in build


def test_agent_reads_method_cards_from_package_root() -> None:
    """方法卡片路径相对 src 的父目录，开发与便携包一致。"""
    main = (Path(__file__).resolve().parents[2] / "src" / "main.py").read_text(encoding="utf-8")
    assert 'Path(__file__).resolve().parents[1] / "knowledge_base_methods_cards"' in main
