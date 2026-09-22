"""RScriptGenerator 单元测试：验证 R 脚本模板生成与参数注入"""


def test_generate_de_template():
    """Test differential expression template generation"""
    from src.control.r_script_generator import RScriptGenerator

    generator = RScriptGenerator()

    code = generator.generate_code("differential_expression", {})

    assert "#!/usr/bin/env Rscript" in code
    assert "read.csv(input_file, row.names = 1)" in code
    assert "write.csv(results, output_file" in code


def test_generate_pathway_template():
    """Test pathway analysis template generation"""
    from src.control.r_script_generator import RScriptGenerator

    generator = RScriptGenerator()

    code = generator.generate_code("pathway_analysis", {})

    assert "#!/usr/bin/env Rscript" in code
    assert "read.csv(input_file)" in code
    assert "KEGG" in code


def test_generate_visualization_template():
    """Test visualization template generation"""
    from src.control.r_script_generator import RScriptGenerator

    generator = RScriptGenerator()

    code = generator.generate_code("visualization", {})

    assert "#!/usr/bin/env Rscript" in code
    assert 'plot_type <- "volcano"' in code
    assert "heatmap(matrix_data" in code


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


def test_visualization_plot_type_param():
    """Test plot_type parameter injection"""
    from src.control.r_script_generator import RScriptGenerator

    generator = RScriptGenerator()

    # 默认值为 volcano
    default_code = generator.generate_code("visualization", {})
    assert 'plot_type <- "volcano"' in default_code

    # 传入 heatmap 时正确注入
    heatmap_code = generator.generate_code("visualization", {"plot_type": "heatmap"})
    assert 'plot_type <- "heatmap"' in heatmap_code


def test_unknown_analysis_type():
    """Test unknown analysis type returns unsupported message"""
    from src.control.r_script_generator import RScriptGenerator

    generator = RScriptGenerator()

    code = generator.generate_code("unknown_type", {})

    assert code == "# 不支持的分析类型: unknown_type"


def test_visualization_template_brace_escaping():
    """Test f-string brace escaping renders valid R braces"""
    from src.control.r_script_generator import RScriptGenerator

    generator = RScriptGenerator()

    code = generator.generate_code("visualization", {})

    # f-string 的 {{/}} 应正确渲染为 R 的 {/}，且配对数量一致
    assert code.count("{") == code.count("}")
    assert "if (!(plot_type %in%" in code
    assert "} else {" in code


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
