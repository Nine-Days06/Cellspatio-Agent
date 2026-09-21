"""数据资产落盘与缓存"""
import json
from pathlib import Path
from typing import Any


class FetcherStorage:
    """管理公共数据资产的本地落盘与缓存"""

    def __init__(self, base_dir: str | Path = "data/raw", meta_dir: str | Path | None = None):
        self.base_dir = Path(base_dir)
        self.meta_dir = Path(meta_dir) if meta_dir else self.base_dir / ".meta"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.meta_dir.mkdir(parents=True, exist_ok=True)

    def _asset_dir(self, source: str, asset_id: str) -> Path:
        """资产目录：data/raw/<source>/<asset_id>/"""
        return self.base_dir / source / asset_id

    def save(self, source: str, asset_id: str, data: bytes, filename: str | None = None) -> Path:
        """保存字节内容，已存在则直接返回不覆盖"""
        asset_dir = self._asset_dir(source, asset_id)
        asset_dir.mkdir(parents=True, exist_ok=True)
        path = asset_dir / (filename or f"{asset_id}.raw")
        if path.exists():
            return path
        path.write_bytes(data)
        return path

    def exists(self, source: str, asset_id: str, filename: str | None = None) -> bool:
        """资产是否已存在"""
        return self.get_path(source, asset_id, filename=filename).exists()

    def get_path(self, source: str, asset_id: str, filename: str | None = None) -> Path:
        """计算资产路径（不保证存在）"""
        return self._asset_dir(source, asset_id) / (filename or f"{asset_id}.raw")

    def save_meta(self, source: str, asset_id: str, info: dict[str, Any]) -> Path:
        """保存资产元数据 JSON"""
        meta_file = self.meta_dir / f"{source}_{asset_id}.json"
        meta_file.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta_file

    def load_meta(self, source: str, asset_id: str) -> dict[str, Any] | None:
        """读取资产元数据，不存在返回 None"""
        meta_file = self.meta_dir / f"{source}_{asset_id}.json"
        if not meta_file.exists():
            return None
        return json.loads(meta_file.read_text(encoding="utf-8"))

    def list_assets(self, source: str | None = None) -> list[str]:
        """列出全部或某来源下的资产文件（相对 base_dir 的路径）"""
        if source is None:
            base = self.base_dir
            prefix = ""
        else:
            base = self.base_dir / source
            prefix = f"{source}/"
        if not base.exists():
            return []

        def walk(dir_: Path, prefix: str = "") -> list[str]:
            assets = []
            for sub in dir_.iterdir():
                if sub.is_dir():
                    if sub == self.meta_dir:
                        continue
                    assets.extend(walk(sub, f"{prefix}{sub.name}/"))
                else:
                    assets.append(f"{prefix}{sub.name}")
            return assets

        return sorted(walk(base, prefix))