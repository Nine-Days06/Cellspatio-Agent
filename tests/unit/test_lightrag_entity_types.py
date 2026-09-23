"""LightRAG 实体类型引导测试：验证 entity_types_guidance 包含单细胞与空间类型"""


def test_entity_guidance_includes_spatial_and_cell_types():
    import inspect

    from src.knowledge import lightrag_client as m

    # 从模块源码提取 addon 字符串（避免初始化 LightRAG）
    text = inspect.getsource(m)
    assert "CellType" in text
    assert "CellState" in text
    assert "SpatialSite" in text
    assert "Dataset" in text
    assert "single-cell" in text.lower() or "单细胞" in text
    assert "spatial" in text.lower() or "空间" in text