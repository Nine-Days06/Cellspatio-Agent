"""性能监控与优化模块"""
import functools
import hashlib
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class PerformanceMonitor:
    """性能监控器"""
    
    def __init__(self):
        self.metrics = {}
    
    def record_metric(self, name: str, value: float, unit: str = "seconds"):
        """记录性能指标"""
        if name not in self.metrics:
            self.metrics[name] = []
        
        self.metrics[name].append({
            'value': value,
            'unit': unit,
            'timestamp': time.time()
        })
        
        logger.debug(f"Performance metric recorded: {name} = {value} {unit}")
    
    def get_average(self, name: str) -> float:
        """获取平均性能指标"""
        if name not in self.metrics or not self.metrics[name]:
            return 0.0
        
        values = [m['value'] for m in self.metrics[name]]
        return sum(values) / len(values)
    
    def get_summary(self) -> dict[str, Any]:
        """获取性能摘要"""
        summary = {}
        for name, metrics in self.metrics.items():
            values = [m['value'] for m in metrics]
            summary[name] = {
                'count': len(values),
                'average': sum(values) / len(values) if values else 0,
                'min': min(values) if values else 0,
                'max': max(values) if values else 0,
            }
        return summary
    
    def reset(self):
        """重置所有性能指标"""
        self.metrics = {}


def monitor_performance(func: Callable) -> Callable:
    """性能监控装饰器"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        
        execution_time = end_time - start_time
        func_name = f"{func.__module__}.{func.__qualname__}"
        
        # 获取或创建性能监控器实例
        if not hasattr(wrapper, 'monitor'):
            wrapper.monitor = PerformanceMonitor()
        
        wrapper.monitor.record_metric(func_name, execution_time)
        logger.debug(f"{func_name} 执行时间: {execution_time:.4f} 秒")
        
        return result
    return wrapper


class EnhancedCache:
    """增强的缓存机制，支持过期策略"""
    
    def __init__(self, cache_dir: str | Path | None = None, ttl: int = 3600):
        """
        初始化缓存
        
        Args:
            cache_dir: 缓存目录
            ttl: 缓存过期时间（秒），默认1小时
        """
        self.cache_dir = Path(cache_dir) if cache_dir else Path("cache")
        self.cache_dir.mkdir(exist_ok=True)
        self.ttl = ttl
    
    def _get_cache_path(self, key: str) -> Path:
        """获取缓存文件路径"""
        hash_key = hashlib.sha256(key.encode()).hexdigest()
        return self.cache_dir / f"{hash_key}.json"
    
    def _is_expired(self, cache_path: Path) -> bool:
        """检查缓存是否过期"""
        if not cache_path.exists():
            return True
        
        # 检查文件修改时间
        mtime = cache_path.stat().st_mtime
        current_time = time.time()
        return (current_time - mtime) > self.ttl
    
    def get(self, key: str) -> Any | None:
        """获取缓存数据"""
        cache_path = self._get_cache_path(key)
        
        if self._is_expired(cache_path):
            logger.debug(f"Cache expired for key: {key}")
            return None
        
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                logger.debug(f"Cache hit for key: {key}")
                return data
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to load cache for key {key}: {e}")
            return None
    
    def set(self, key: str, value: Any, ttl: int | None = None):
        """设置缓存数据"""
        cache_path = self._get_cache_path(key)
        
        try:
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(value, f, ensure_ascii=False, indent=2)
            
            logger.debug(f"Cache set for key: {key}")
        except (OSError, TypeError) as e:
            logger.warning(f"Failed to save cache for key {key}: {e}")
    
    def clear(self):
        """清空缓存"""
        for cache_file in self.cache_dir.glob("*.json"):
            cache_file.unlink()
        logger.info("Cache cleared")
    
    def get_cache_stats(self) -> dict[str, Any]:
        """获取缓存统计信息"""
        cache_files = list(self.cache_dir.glob("*.json"))
        total_size = sum(f.stat().st_size for f in cache_files)
        
        return {
            'total_files': len(cache_files),
            'total_size_mb': total_size / (1024 * 1024),
            'cache_dir': str(self.cache_dir),
            'ttl_seconds': self.ttl,
        }
