"""数据资产落盘与缓存"""

import json
from pathlib import Path
from typing import Any


def _safe_segment(value: str, label: str) -> str:
    """资产标识只能是单个路径段，防止远端 ID 穿越目录。"""
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or Path(value).is_absolute()
    ):
        raise ValueError(f"{label} 不能包含路径分隔符: {value!r}")
    return value


class FetcherStorage:
    """管理公共数据资产的本地落盘与缓存"""

    def __init__(self, base_dir: str | Path = "data/raw", meta_dir: str | Path | None = None):
        self.base_dir = Path(base_dir)
        self.meta_dir = Path(meta_dir) if meta_dir else self.base_dir / ".meta"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.meta_dir.mkdir(parents=True, exist_ok=True)

    def _asset_dir(self, source: str, asset_id: str) -> Path:
        """资产目录：data/raw/<source>/<asset_id>/"""
        return self.base_dir / _safe_segment(source, "source") / _safe_segment(asset_id, "asset_id")

    def save(self, source: str, asset_id: str, data: bytes, filename: str | None = None) -> Path:
        """保存字节内容，已存在则直接返回不覆盖"""
        asset_dir = self._asset_dir(source, asset_id)
        asset_dir.mkdir(parents=True, exist_ok=True)
        name = _safe_segment(filename or f"{asset_id}.raw", "filename")
        path = asset_dir / name
        if path.exists():
            return path
        tmp = path.with_name(path.name + ".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        return path

    def exists(self, source: str, asset_id: str, filename: str | None = None) -> bool:
        """资产是否已存在"""
        return self.get_path(source, asset_id, filename=filename).exists()

    def get_path(self, source: str, asset_id: str, filename: str | None = None) -> Path:
        """计算资产路径（不保证存在）"""
        name = _safe_segment(filename or f"{asset_id}.raw", "filename")
        return self._asset_dir(source, asset_id) / name

    def save_meta(self, source: str, asset_id: str, info: dict[str, Any]) -> Path:
        """保存资产元数据 JSON"""
        meta_file = self.meta_dir / self._meta_name(source, asset_id)
        tmp = meta_file.with_name(meta_file.name + ".part")
        tmp.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(meta_file)
        return meta_file

    def load_meta(self, source: str, asset_id: str) -> dict[str, Any] | None:
        """读取资产元数据，不存在返回 None"""
        meta_file = self.meta_dir / self._meta_name(source, asset_id)
        if not meta_file.exists():
            return None
        return json.loads(meta_file.read_text(encoding="utf-8"))

    @staticmethod
    def _meta_name(source: str, asset_id: str) -> str:
        """元数据文件名同样只允许安全段。"""
        return f"{_safe_segment(source, 'source')}_{_safe_segment(asset_id, 'asset_id')}.json"

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
