"""公共证据数据备份与恢复。

python -m src.cli.evidence_backup backup --root <data> --out <backup-dir>
python -m src.cli.evidence_backup restore --backup <backup-dir> --root <new-data>

备份先停服或在低峰执行；恢复前必须停服。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.evidence.accounts import AccountStore
from src.evidence.governance import GovernanceStore, public_evidence_dir

BACKUP_SCHEMA_VERSION = "cellspatio-evidence-backup/1.0"


def _sha256(path: Path) -> str:
    """流式计算文件 SHA-256。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot(source: Path, target: Path) -> None:
    """用 SQLite backup API 生成一致性快照，兼容 WAL。"""
    source_conn = sqlite3.connect(source)
    try:
        target_conn = sqlite3.connect(target)
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
    finally:
        source_conn.close()


def _manifest(path: Path) -> dict[str, Any]:
    """读取并校验备份 manifest。"""
    if not path.is_file():
        raise SystemExit(f"备份缺少 manifest.json: {path.parent}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SystemExit(f"manifest 不是有效 JSON: {path}") from exc
    if data.get("schema_version") != BACKUP_SCHEMA_VERSION:
        raise SystemExit(f"备份 manifest 版本不支持: {data.get('schema_version')}")
    databases = data.get("databases")
    if not isinstance(databases, dict) or not databases:
        raise SystemExit("备份 manifest 没有数据库条目")
    if not all(
        isinstance(name, str) and isinstance(digest, str) for name, digest in databases.items()
    ):
        raise SystemExit("备份 manifest 的数据库条目无效")
    cards = data.get("cards")
    if not isinstance(cards, int) or cards < 0:
        raise SystemExit("备份 manifest 的卡数量无效")
    cards_sha256 = data.get("cards_sha256")
    if not isinstance(cards_sha256, str) or not cards_sha256:
        raise SystemExit("备份 manifest 缺少卡内容摘要")
    return data


def _layout(root: Path) -> tuple[dict[str, Path], Path]:
    """从存储类取数据库与卡目录，避免复制存储布局。"""
    public_dir = public_evidence_dir(root)
    governance = GovernanceStore(public_dir)
    accounts = AccountStore(public_dir)
    return {
        "governance.db": governance.db_path,
        "accounts.db": accounts.db_path,
    }, governance.cards_dir


def _cards_digest(cards: Path) -> str:
    """卡目录聚合摘要：文件名 + 内容哈希按序拼接。"""
    digest = hashlib.sha256()
    if not cards.is_dir():
        return digest.hexdigest()
    for path in sorted(cards.rglob("*.json")):
        digest.update(path.relative_to(cards).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def backup(root: Path, out: Path) -> None:
    """备份数据库、不可变卡与 manifest。"""
    public_dir = public_evidence_dir(root)
    if not public_dir.is_dir():
        raise SystemExit(f"公共证据目录不存在: {public_dir}")
    if out.exists() and not out.is_dir():
        raise SystemExit(f"备份目标不是目录: {out}")
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"备份目录必须为空: {out}")
    if out.resolve().is_relative_to(public_dir.resolve()):
        raise SystemExit("备份目录不能放在 public_evidence 内")
    out.mkdir(parents=True, exist_ok=True)

    paths, cards = _layout(root)
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise SystemExit("缺少数据库，先启动服务并开户: " + "、".join(missing))

    databases: dict[str, str] = {}
    for name, source in paths.items():
        target = out / name
        _snapshot(source, target)
        databases[name] = _sha256(target)

    card_count = 0
    if cards.is_dir():
        shutil.copytree(cards, out / "cards")
        card_count = sum(1 for _ in (out / "cards").rglob("*.json"))

    manifest = {
        "schema_version": BACKUP_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "databases": databases,
        "cards": card_count,
        "cards_sha256": _cards_digest(out / "cards"),
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"备份完成: {out}（{len(databases)} 个数据库，{card_count} 张卡）")


def restore(backup_dir: Path, root: Path) -> None:
    """把备份恢复到空的数据根；目标非空或哈希不符即拒绝。"""
    manifest = _manifest(backup_dir / "manifest.json")
    target = public_evidence_dir(root)
    if target.exists() and any(target.iterdir()):
        raise SystemExit(f"目标 public_evidence 必须为空；恢复前请停服: {target}")

    for name, expected in manifest["databases"].items():
        source = backup_dir / name
        if not source.is_file():
            raise SystemExit(f"备份缺少数据库: {name}")
        if _sha256(source) != expected:
            raise SystemExit(f"备份数据库校验失败: {name}")

    source_cards = backup_dir / "cards"
    card_count = sum(1 for _ in source_cards.rglob("*.json")) if source_cards.is_dir() else 0
    if card_count != manifest["cards"]:
        raise SystemExit(f"备份卡数量与 manifest 不一致: {card_count} != {manifest['cards']}")
    if _cards_digest(source_cards) != manifest["cards_sha256"]:
        raise SystemExit("备份卡内容校验失败")

    target.mkdir(parents=True, exist_ok=True)
    for name in manifest["databases"]:
        shutil.copy2(backup_dir / name, target / name)
    if source_cards.is_dir():
        shutil.copytree(source_cards, target / "cards", dirs_exist_ok=True)
    print(f"恢复完成: {target}（{len(manifest['databases'])} 个数据库，{card_count} 张卡）")


def main(argv: list[str] | None = None) -> int:
    """解析参数并执行备份或恢复。"""
    parser = argparse.ArgumentParser(description="CellSpatio 公共证据备份与恢复")
    parser.add_argument("--root", default=None, help="数据根：backup 的源 / restore 的目标")
    sub = parser.add_subparsers(dest="command", required=True)

    backup_parser = sub.add_parser("backup", help="备份到空目录")
    backup_parser.add_argument("--out", required=True, help="备份目录")
    backup_parser.add_argument("--root", default=argparse.SUPPRESS, help="数据根（也可写在前面）")

    restore_parser = sub.add_parser("restore", help="从备份恢复到空数据根")
    restore_parser.add_argument("--backup", required=True, help="备份目录")
    restore_parser.add_argument("--root", default=argparse.SUPPRESS, help="数据根（也可写在前面）")

    args = parser.parse_args(argv)
    if not args.root:
        raise SystemExit("--root 必填")
    root = Path(args.root)
    if args.command == "backup":
        backup(root, Path(args.out))
    else:
        restore(Path(args.backup), root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
