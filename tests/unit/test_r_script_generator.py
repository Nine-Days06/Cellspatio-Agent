"""RScriptGenerator 单元测试：验证 R 脚本模板生成与参数注入"""


def test_generate_de_template():
    """Test differential expression template generation"""
    from src.control.r_script_generator import RScriptGenerator

    generator = RScriptGenerator()

    code = generator.generate_code("differential_expression", {})

    assert "#!/usr/bin/env Rscript" in code
    assert "DESeqDataSetFromMatrix" in code
    assert "DESeq(" in code
    assert "results(" in code
    assert 'input_file <- "input.csv"' in code
    assert 'output_file <- "output.csv"' in code


def test_unimplemented_analysis_types_have_no_template():
    """通路富集与独立绘图没有执行链，不得再返回"看起来能跑"的模板。"""
    from src.control.r_script_generator import RScriptGenerator

    generator = RScriptGenerator()

    for analysis_type in ("pathway_analysis", "visualization"):
        code = generator.generate_code(analysis_type, {"plot_type": "volcano"})
        assert code == f"# 不支持的分析类型: {analysis_type}"


def test_de_template_param_injection():
    """Test parameter injection into DE template"""
    from src.control.r_script_generator import RScriptGenerator

    generator = RScriptGenerator()

    code = generator.generate_code(
        "differential_expression",
        {
            "input_file": "mydata.csv",
            "output_file": "myresults.tsv",
        },
    )

    assert 'input_file <- "mydata.csv"' in code
    assert 'output_file <- "myresults.tsv"' in code
    assert "DESeqDataSetFromMatrix" in code
    assert "DESeq(" in code
    assert "results(" in code


def test_windows_paths_are_escaped_in_templates():
    """Windows 反斜杠必须转义，否则 R 会把路径解析成转义序列。"""
    from src.control.r_script_generator import RScriptGenerator

    generator = RScriptGenerator()
    code = generator.generate_code(
        "differential_expression",
        {
            "input_file": r"D:\data\counts.csv",
            "output_file": r"D:\data\result.csv",
            "group_csv": r"D:\data\groups.csv",
        },
    )

    assert 'input_file <- "D:\\\\data\\\\counts.csv"' in code
    assert 'output_file <- "D:\\\\data\\\\result.csv"' in code
    assert 'read.csv("D:\\\\data\\\\groups.csv"' in code


def test_unknown_analysis_type():
    """Test unknown analysis type returns unsupported message"""
    from src.control.r_script_generator import RScriptGenerator

    generator = RScriptGenerator()

    code = generator.generate_code("unknown_type", {})

    assert code == "# 不支持的分析类型: unknown_type"


def test_generate_code_injects_method_context():
    from src.control.r_script_generator import RScriptGenerator

    gen = RScriptGenerator()
    code = gen.generate_code(
        "differential_expression",
        {"input_file": "a.csv", "output_file": "b.csv"},
        method_context="# DESeq2 卡片\n# 不要用 TPM",
    )
    assert "方法学参考（自动生成，勿删）" in code
    assert "不要用 TPM" in code


def test_generate_code_without_method_context_has_no_marker():
    from src.control.r_script_generator import RScriptGenerator

    gen = RScriptGenerator()
    code = gen.generate_code(
        "differential_expression",
        {"input_file": "a.csv", "output_file": "b.csv"},
    )
    assert "方法学参考（自动生成，勿删）" not in code


def test_de_template_is_real_deseq2_not_mock():
    from src.control.r_script_generator import RScriptGenerator

    code = RScriptGenerator().generate_code(
        "differential_expression",
        {"input_file": "c.csv", "output_file": "o.csv"},
    )
    assert "DESeqDataSetFromMatrix" in code
    assert "DESeq(" in code
    assert "results(" in code
    assert "log2FC = 0" not in code
    assert "pvalue = 1" not in code
    assert 'input_file <- "c.csv"' in code
    assert 'output_file <- "o.csv"' in code


def test_spatial_visium_template():
    from src.control.r_script_generator import RScriptGenerator

    code = RScriptGenerator().generate_code(
        "spatial",
        {"input_file": "spatial_data", "output_file": "sp.csv", "plot_file": "spatial_plot.png"},
    )
    assert "Load10X_Spatial" in code
    assert "SpatialDimPlot" in code
    assert "spatial 模板尚未实现" not in code
    assert 'output_file <- "sp.csv"' in code
    assert 'plot_file <- "spatial_plot.png"' in code
