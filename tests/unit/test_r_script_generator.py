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


def test_generate_pathway_template():
    """Test pathway analysis template generation"""
    from src.control.r_script_generator import RScriptGenerator

    generator = RScriptGenerator()

    code = generator.generate_code("pathway_analysis", {})

    assert "#!/usr/bin/env Rscript" in code
    assert "enrichKEGG" in code
    assert "clusterProfiler" in code
    assert "org.Hs.eg.db" in code
    assert 'input_file <- "de_results.csv"' in code
    assert 'output_file <- "pathway_enrichment.csv"' in code


def test_generate_visualization_template():
    """Test visualization template generation"""
    from src.control.r_script_generator import RScriptGenerator

    generator = RScriptGenerator()

    code = generator.generate_code("visualization", {})

    assert "#!/usr/bin/env Rscript" in code
    assert 'plot_type <- "volcano"' in code
    # 真实 base R 火山图逻辑：读取 CSV、校验列、png()、plot()、abline()
    assert "read.csv(input_file" in code
    assert "log2FC" in code
    assert "padj" in code
    assert "png(output_file)" in code
    assert "plot(" in code
    assert "abline(" in code
    assert "dev.off()" in code
    # 热图分支也存在
    assert "heatmap(matrix_data" in code
    assert "heat.colors(100)" in code
    # PCA 分支也存在
    assert "prcomp(" in code


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


def test_pathway_template_is_real_clusterprofiler():
    from src.control.r_script_generator import RScriptGenerator

    code = RScriptGenerator().generate_code(
        "pathway_analysis",
        {"input_file": "de.csv", "output_file": "pw.csv"},
    )
    assert "enrichKEGG" in code
    assert "clusterProfiler" in code
    assert "pvalue = numeric()" not in code
    assert 'input_file <- "de.csv"' in code


def test_spatial_visium_template():
    from src.control.r_script_generator import RScriptGenerator

    code = RScriptGenerator().generate_code(
        "spatial",
        {"input_file": "spatial_data", "output_file": "sp.csv",
         "plot_file": "spatial_plot.png"},
    )
    assert "Load10X_Spatial" in code
    assert "SpatialDimPlot" in code
    assert "spatial 模板尚未实现" not in code
    assert 'output_file <- "sp.csv"' in code
    assert 'plot_file <- "spatial_plot.png"' in code


def test_visualization_template_is_real_base_r_not_mock():
    """Test visualization template uses real base R plotting, not rnorm stub"""
    from src.control.r_script_generator import RScriptGenerator

    code = RScriptGenerator().generate_code(
        "visualization",
        {"input_file": "data.csv", "output_file": "plot.png", "plot_type": "volcano"},
    )
    # 真实绘图逻辑
    assert "read.csv(input_file" in code
    assert "log2FC" in code
    assert "padj" in code
    assert "png(output_file)" in code
    assert "plot(" in code
    assert "abline(" in code
    assert "dev.off()" in code
    assert "cat(" in code
    # 无 rnorm 假数据
    assert "rnorm(" not in code
    assert "matrix(rnorm" not in code
    # 参数注入正确
    assert 'input_file <- "data.csv"' in code
    assert 'output_file <- "plot.png"' in code
    assert 'plot_type <- "volcano"' in code
