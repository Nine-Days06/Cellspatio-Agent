"""知识库导入模块 - 从独立文献处理项目导入数据"""
import hashlib
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.knowledge.article_text import convert_article_to_text

logger = logging.getLogger(__name__)


def _file_sha256(path: Path) -> str:
    """计算文件内容的 SHA-256（十六进制），供台账审计记录使用"""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ImportLedger:
    """已导入 CSV 台账（JSON 文件），增量导入的持久化判重依据。

    为什么用「文件名台账」而不是再建一份 PMID 台账：
    pubmed-etl 导出侧已经用 exported_pmids.txt 做过 PMID 级增量，每个
    articles_<时间戳>.csv 只含全新文章且文件名唯一；若主项目再维护一份 PMID
    集合，就会出现两个相互竞争的去重事实来源。因此这里以「批次文件名」为唯一键，
    台账只回答一个问题：这个 CSV 批次是否已经导入过。

    设计策略（边界情况的取舍）：
    - 台账文件损坏 / 不可读：记录 warning 并按空台账处理——宁可重复导入
      （LightRAG 会按内容哈希与文件名静默去重）也不中断同步流程。
    - 台账条目对应的 CSV 已被删除：条目保留即可，不参与任何文件访问，不影响流程。
    - CSV 在导入之后被修改：不自动重新导入。文件名是台账唯一键，且 LightRAG
      对相同内容/相同文件名的重复提交只会去重、不会更新旧文档，自动重导既无收益
      也观测不到效果；导入时记录的 sha256 仅作人工审计。需要重导时删除对应台账
      条目，或使用 --reimport-all / --reimport-all 标志做有意的全量重导。
    - 删除或清空整个台账文件：等价于「全部未导入」，触发一次有意的全量重导。
    """

    #: 台账文件名，固定位于导入目录下（人类可直接查看与编辑）
    LEDGER_FILENAME = "import_ledger.json"

    def __init__(self, ledger_path: str | Path) -> None:
        """加载台账；损坏或不可读时回退为空台账并记录 warning"""
        self.path = Path(ledger_path)
        self._entries = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        """从磁盘读取台账条目；任何读取失败均返回空字典（不抛异常）"""
        if not self.path.exists():
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.warning(
                "导入台账损坏或不可读，按空台账处理（将重新导入全部文件）: %s (%s)",
                self.path, e,
            )
            return {}
        if not isinstance(data, dict) or not isinstance(data.get("files", {}), dict):
            logger.warning(
                "导入台账结构非法，按空台账处理（将重新导入全部文件）: %s", self.path
            )
            return {}
        files = data.get("files", {})
        return {
            name: entry for name, entry in files.items()
            if isinstance(name, str) and isinstance(entry, dict)
        }

    def is_imported(self, filename: str) -> bool:
        """判断某个文件名（批次）是否已在台账中记录"""
        return filename in self._entries

    def get_entry(self, filename: str) -> dict[str, Any] | None:
        """返回某个文件名的台账条目（含 imported_at/count/sha256），无则 None"""
        return self._entries.get(filename)

    def record(self, filename: str, count: int, sha256: str) -> None:
        """记录一个已成功导入的文件并立即落盘

        Args:
            filename: CSV 文件名（批次唯一标识）
            count: 本次从该文件提交的文章数
            sha256: 文件内容哈希，供人工审计「导入后是否被修改」
        """
        self._entries[filename] = {
            "imported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "count": count,
            "sha256": sha256,
        }
        self._save()

    def _save(self) -> None:
        """把台账写回磁盘；写失败只记录 error（下次运行会重新导入，不丢数据）"""
        payload = {"version": 1, "files": self._entries}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except (OSError, UnicodeEncodeError) as e:
            logger.error(
                "写入导入台账失败，本次导入结果未持久化（下次将重复导入）: %s (%s)",
                self.path, e,
            )


class KnowledgeImporter:
    """知识库导入器，支持多种格式导入"""
    
    def __init__(self, lightrag_client):
        self.client = lightrag_client
    
    def import_from_json(self, json_path: str) -> dict[str, Any]:
        """从 JSON 文件导入知识库"""
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            articles = data if isinstance(data, list) else data.get("articles", [])
            
            texts = [convert_article_to_text(a) for a in articles]
            count = self.client.insert_documents(texts)
            
            logger.info(f"Imported {count} articles from JSON: {json_path}")
            return {"success": True, "count": count, "source": json_path}
            
        except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to import from JSON: {e}")
            return {"success": False, "error": str(e)}
    
    def import_from_sqlite(self, db_path: str, query: str | None = None) -> dict[str, Any]:
        """从 SQLite 数据库导入知识库"""
        try:
            conn = sqlite3.connect(db_path)
            
            if query is None:
                query = "SELECT * FROM articles WHERE human_review = 'Y' OR human_review IS NULL"
            
            df = pd.read_sql_query(query, conn)
            conn.close()
            
            texts = [convert_article_to_text(row.to_dict()) for _, row in df.iterrows()]
            count = self.client.insert_documents(texts)
            
            logger.info(f"Imported {count} articles from SQLite: {db_path}")
            return {"success": True, "count": count, "source": db_path}
            
        except (FileNotFoundError, sqlite3.Error, pd.errors.DatabaseError) as e:
            logger.error(f"Failed to import from SQLite: {e}")
            return {"success": False, "error": str(e)}
    
    def import_from_csv(self, csv_path: str) -> dict[str, Any]:
        """从 CSV 文件导入知识库"""
        try:
            df = pd.read_csv(csv_path)
            
            texts = [convert_article_to_text(row.to_dict()) for _, row in df.iterrows()]
            count = self.client.insert_documents(texts)
            
            logger.info(f"Imported {count} articles from CSV: {csv_path}")
            return {"success": True, "count": count, "source": csv_path}
            
        except (FileNotFoundError, pd.errors.EmptyDataError, pd.errors.ParserError) as e:
            logger.error(f"Failed to import from CSV: {e}")
            return {"success": False, "error": str(e)}
    
    def import_from_directory(self, dir_path: str, file_types: list[str] | None = None) -> dict[str, Any]:
        """从目录批量导入（全量扫描，不查台账，历史行为保持不变）

        需要去重时请使用 import_incremental；此处始终跳过台账文件本身，
        避免把 import_ledger.json 当作文献 JSON 导入。
        """
        if file_types is None:
            file_types = ['json', 'csv']
        
        dir_path = Path(dir_path)
        total_count = 0
        imported_files = []
        
        for file_type in file_types:
            if file_type == 'json':
                pattern = "*.json"
            elif file_type == 'csv':
                pattern = "*.csv"
            elif file_type == 'sqlite':
                pattern = "*.db"
            else:
                continue
            
            for file_path in dir_path.glob(pattern):
                if file_path.name == ImportLedger.LEDGER_FILENAME:
                    continue
                result = self.import_from_file(str(file_path))
                if result.get("success"):
                    total_count += result.get("count", 0)
                    imported_files.append(str(file_path))
        
        logger.info(f"Imported {total_count} articles from {len(imported_files)} files")
        return {"success": True, "total_count": total_count, "imported_files": imported_files}

    def import_incremental(
        self,
        dir_path: str,
        file_types: list[str] | None = None,
        *,
        files: list[str] | None = None,
        reimport_all: bool = False,
        ledger_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """增量导入：仅导入台账未记录的文件，重复执行为空操作。

        Args:
            dir_path: 导入目录（台账默认写在该目录下的 import_ledger.json）
            file_types: files 为 None 时的目录扫描类型，默认 ['json', 'csv']
            files: 只导入这些相对 dir_path 的文件名（sync_pubmed 单文件导入用）；
                None 表示扫描整个目录
            reimport_all: True 时忽略台账过滤，全量重导候选文件（有意的全量刷新）
            ledger_path: 台账路径覆盖，默认 dir_path/import_ledger.json

        Returns:
            - success: 本次没有任何文件导入失败
            - total_count: 实际提交给 LightRAG 的文章总数（即「扫描提交数」）
            - new_count: 其中来自「台账未记录」文件的文章数——本次真正的新批次
              提交量，也就是对外报告的 imported 数
            - imported_files / skipped_files / failed_files

        计数口径的诚实说明：new_count 统计的是「首次提交给 LightRAG」的文章，
        不等于 LightRAG 实际新增存储的文档数——LightRAG 1.5.7 会在 pipeline 内部
        按 content_hash 与文件名静默去重，且不通过 SDK 返回逐文档的去重信号，
        因此实际落库增量无法在本层观测，这里不虚构该数字。
        """
        dir_path = Path(dir_path)
        ledger = ImportLedger(ledger_path or dir_path / ImportLedger.LEDGER_FILENAME)

        if files is not None:
            candidates = [dir_path / name for name in files]
        else:
            if file_types is None:
                file_types = ['json', 'csv']
            patterns: dict[str, str] = {'json': '*.json', 'csv': '*.csv', 'sqlite': '*.db'}
            candidates = sorted(
                path
                for file_type in file_types
                for path in dir_path.glob(patterns.get(file_type, '__no_match__'))
                if path.name != ImportLedger.LEDGER_FILENAME
            )

        total_count = 0
        new_count = 0
        imported_files: list[str] = []
        skipped_files: list[str] = []
        failed_files: list[str] = []

        for path in candidates:
            name = path.name
            if not path.exists():
                logger.error(f"待导入文件不存在: {path}")
                failed_files.append(name)
                continue

            sha256 = _file_sha256(path)
            previously_imported = ledger.is_imported(name)
            if previously_imported:
                entry = ledger.get_entry(name) or {}
                if entry.get("sha256") and entry["sha256"] != sha256:
                    logger.warning(
                        "CSV 在导入后被修改，按文件名台账策略不自动重导: %s"
                        "（如需重导请删除该台账条目或使用 --reimport-all）",
                        name,
                    )
                if not reimport_all:
                    skipped_files.append(name)
                    continue

            result = self.import_from_file(str(path))
            if not result.get("success"):
                logger.error(f"导入文件失败: {path} -> {result.get('error')}")
                failed_files.append(name)
                continue

            count = int(result.get("count", 0))
            total_count += count
            if not previously_imported:
                new_count += count
            imported_files.append(str(path))
            ledger.record(name, count, sha256)

        success = not failed_files
        result: dict[str, Any] = {
            "success": success,
            "total_count": total_count,
            "new_count": new_count,
            "imported_files": imported_files,
            "skipped_files": skipped_files,
            "failed_files": failed_files,
        }
        if failed_files:
            result["error"] = f"failed to import: {', '.join(failed_files)}"
        logger.info(
            "增量导入完成: 提交 %d 篇（新批次 %d 篇），"
            "导入 %d 个文件，跳过 %d 个已导入文件，失败 %d 个",
            total_count, new_count, len(imported_files),
            len(skipped_files), len(failed_files),
        )
        return result
    
    def import_from_file(self, file_path: str) -> dict[str, Any]:
        """根据文件类型自动选择导入方法"""
        file_path = Path(file_path)
        
        if file_path.suffix.lower() == '.json':
            return self.import_from_json(str(file_path))
        elif file_path.suffix.lower() == '.csv':
            return self.import_from_csv(str(file_path))
        elif file_path.suffix.lower() in ['.db', '.sqlite']:
            return self.import_from_sqlite(str(file_path))
        else:
            return {"success": False, "error": f"Unsupported file type: {file_path.suffix}"}