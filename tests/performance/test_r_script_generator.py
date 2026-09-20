"""RScriptGenerator 性能测试：验证 R 脚本生成性能，不依赖 R 环境"""
import time

import pytest

from src.control.r_script_generator import RScriptGenerator


def test_de_template_batch_generation_performance():
    """测试差异表达模板批量生成性能"""
    generator = RScriptGenerator()
    params = {"input_file": "data.csv", "output_file": "results.csv"}

    start_time = time.time()
    for _ in range(1000):
        generator.generate_code("differential_expression", params)
    elapsed = time.time() - start_time

    print(f"\n批量生成 1000 次 DE 模板耗时: {elapsed:.4f} 秒")

    assert elapsed < 5.0, "DE 模板批量生成性能过低"


def test_template_generation_time_comparison():
    """测试三种模板生成耗时对比"""
    generator = RScriptGenerator()
    templates = {
        "differential_expression": {"input_file": "data.csv", "output_file": "results.csv"},
        "pathway_analysis": {},
        "visualization": {"input_file": "data.csv", "output_file": "output.png", "plot_type": "volcano"},
    }

    for analysis_type, params in templates.items():
        start_time = time.time()
        for _ in range(100):
            generator.generate_code(analysis_type, params)
        elapsed = time.time() - start_time

        print(f"{analysis_type} 生成 100 次耗时: {elapsed:.4f} 秒")

        assert elapsed < 2.0, f"{analysis_type} 模板生成性能过低"


def test_generate_code_deterministic():
    """测试相同参数下模板生成输出一致"""
    generator = RScriptGenerator()
    params = {"input_file": "data.csv", "output_file": "results.csv", "plot_type": "volcano"}

    for analysis_type in ("differential_expression", "pathway_analysis", "visualization"):
        code1 = generator.generate_code(analysis_type, params)
        code2 = generator.generate_code(analysis_type, params)
        assert code1 == code2, f"{analysis_type} 模板生成不一致"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])