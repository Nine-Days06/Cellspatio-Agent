"""缓存性能测试"""
import sys
import tempfile
import time
from pathlib import Path

import pytest

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.performance import EnhancedCache, PerformanceMonitor, monitor_performance


def test_cache_write_performance():
    """测试缓存写入性能"""
    with tempfile.TemporaryDirectory() as temp_dir:
        cache = EnhancedCache(temp_dir, ttl=60)
        
        start_time = time.time()
        for i in range(1000):
            cache.set(f"key_{i}", {"data": f"value_{i}", "index": i})
        write_time = time.time() - start_time
        
        print(f"\n写入 1000 条缓存耗时: {write_time:.4f} 秒")
        
        # 性能断言
        assert write_time < 10.0, "缓存写入性能过低"
        assert cache.get_cache_stats()['total_files'] == 1000


def test_cache_read_performance():
    """测试缓存读取性能"""
    with tempfile.TemporaryDirectory() as temp_dir:
        cache = EnhancedCache(temp_dir, ttl=3600)
        
        # 先写入数据
        for i in range(1000):
            cache.set(f"key_{i}", {"data": f"value_{i}", "index": i})
        
        start_time = time.time()
        for i in range(1000):
            result = cache.get(f"key_{i}")
            assert result is not None, f"缓存读取失败: key_{i}"
        read_time = time.time() - start_time
        
        print(f"\n读取 1000 条缓存耗时: {read_time:.4f} 秒")
        
        # 性能断言（Windows 系统 I/O 可能较慢）
        assert read_time < 15.0, "缓存读取性能过低"


def test_cache_expiration():
    """测试缓存过期功能"""
    with tempfile.TemporaryDirectory() as temp_dir:
        cache = EnhancedCache(temp_dir, ttl=1)  # 1秒过期
        
        # 写入数据
        cache.set("test_key", "test_value")
        assert cache.get("test_key") == "test_value"
        
        # 等待过期
        time.sleep(1.1)
        result = cache.get("test_key")
        assert result is None, "缓存过期失败"


def test_performance_monitor():
    """测试性能监控器"""
    monitor = PerformanceMonitor()
    
    # 记录一些指标
    monitor.record_metric("test_metric", 0.1)
    monitor.record_metric("test_metric", 0.2)
    monitor.record_metric("test_metric", 0.3)
    
    # 获取平均值
    avg = monitor.get_average("test_metric")
    assert abs(avg - 0.2) < 0.001, "平均值计算错误"
    
    # 获取摘要
    summary = monitor.get_summary()
    assert "test_metric" in summary
    assert summary["test_metric"]["count"] == 3
    assert abs(summary["test_metric"]["average"] - 0.2) < 0.001


def test_performance_monitor_decorator():
    """测试性能监控装饰器"""
    @monitor_performance
    def test_function():
        time.sleep(0.01)
        return "done"
    
    result = test_function()
    assert result == "done"
    
    # 检查是否记录了性能指标
    assert hasattr(test_function, 'monitor')
    summary = test_function.monitor.get_summary()
    assert len(summary) > 0


def test_cache_stats():
    """测试缓存统计功能"""
    with tempfile.TemporaryDirectory() as temp_dir:
        cache = EnhancedCache(temp_dir, ttl=60)
        
        # 写入一些数据
        for i in range(10):
            cache.set(f"key_{i}", f"value_{i}")
        
        stats = cache.get_cache_stats()
        
        assert stats['total_files'] == 10
        assert stats['cache_dir'] == temp_dir
        assert stats['ttl_seconds'] == 60
        assert stats['total_size_mb'] >= 0


def test_cache_clear():
    """测试缓存清空功能"""
    with tempfile.TemporaryDirectory() as temp_dir:
        cache = EnhancedCache(temp_dir, ttl=60)
        
        # 写入数据
        for i in range(10):
            cache.set(f"key_{i}", f"value_{i}")
        
        assert cache.get_cache_stats()['total_files'] == 10
        
        # 清空缓存
        cache.clear()
        
        assert cache.get_cache_stats()['total_files'] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
