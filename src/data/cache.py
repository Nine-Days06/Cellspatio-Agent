import hashlib
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

class Cache:
    """简单的文件缓存机制"""
    
    def __init__(self, cache_dir: str | Path | None = None):
        self.cache_dir = Path(cache_dir) if cache_dir else Path("cache")
        self.cache_dir.mkdir(exist_ok=True)
    
    def _get_cache_key(self, key: str) -> Path:
        """生成缓存文件路径"""
        hash_key = hashlib.sha256(key.encode()).hexdigest()
        return self.cache_dir / f"{hash_key}.json"
    
    def get(self, key: str) -> Any | None:
        """获取缓存数据"""
        cache_path = self._get_cache_key(key)
        if cache_path.exists():
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"Failed to load cache for key {key}: {e}")
        return None
    
    def set(self, key: str, value: Any):
        """设置缓存数据"""
        cache_path = self._get_cache_key(key)
        try:
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(value, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.warning(f"Failed to save cache for key {key}: {e}")
    
    def clear(self):
        """清空缓存"""
        for cache_file in self.cache_dir.glob("*.json"):
            cache_file.unlink()