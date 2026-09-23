"""RScriptGenerator LLM 动态生成路径"""
from src.control.r_script_generator import RScriptGenerator


def _fake_llm(content: str):
    class Msg:
        def __init__(self, c):
            self.content = c

    class Choice:
        def __init__(self, c):
            self.message = Msg(c)

    class Resp:
        def __init__(self, c):
            self.choices = [Choice(c)]

    class Completions:
        def __init__(self, client):
            self._client = client

        def create(self, **kwargs):
            self._client.prompts.append(kwargs)
            return Resp(self._client.content)

    class Client:
        def __init__(self):
            self.content = content
            self.prompts = []
            self.chat = type("Chat", (), {})()
            self.chat.completions = Completions(self)

    return Client()


def test_llm_path_strips_fences_and_injects_method_context():
    llm = _fake_llm("```r\n#!/usr/bin/env Rscript\ncat('hi')\n```")
    gen = RScriptGenerator(llm_client=llm, model="m")
    code = gen.generate_code(
        "differential_expression",
        {"input_file": "a.csv", "output_file": "b.csv"},
        method_context="# DESeq2 要点",
    )
    assert code.startswith("#!/usr/bin/env Rscript")
    assert "```" not in code
    assert "方法学参考（自动生成，勿删）" in code
    assert llm.prompts, "应调用 LLM"
    joined = str(llm.prompts)
    assert "DESeq2" in joined
    assert "a.csv" in joined


def test_llm_failure_falls_back_to_template():
    class Boom:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise RuntimeError("down")

    gen = RScriptGenerator(llm_client=Boom(), model="m")
    code = gen.generate_code(
        "differential_expression", {"input_file": "a.csv", "output_file": "b.csv"}
    )
    assert "DESeqDataSetFromMatrix" in code  # 真实模板（任务 2）；若任务 1 先行可暂断言 read.csv


def test_no_llm_uses_template():
    gen = RScriptGenerator()
    code = gen.generate_code("visualization", {})
    assert 'plot_type <- "volcano"' in code


def test_unknown_type_unchanged():
    gen = RScriptGenerator()
    assert gen.generate_code("unknown_type", {}) == "# 不支持的分析类型: unknown_type"