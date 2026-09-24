"""ResultExplainer 单元测试"""

from src.analysis.result_explainer import ResultExplainer


def test_fallback_when_no_llm_returns_template_not_placeholder():
    ex = ResultExplainer(llm_client=None)
    out = ex.generate_llm_explanation(
        {"total_genes": 100, "significant_genes": 20, "top_pathways": ["p53"]},
        "差异表达说明",
    )
    assert "待实现" not in out
    assert "20" in out  # 规则模板仍可读


def test_llm_path_uses_knowledge_context_and_llm():
    captured = {}

    class FakeKB:
        def query(self, q, mode="hybrid"):
            captured["q"] = q
            return "TP53 是肿瘤抑制因子（来自知识库）"

    class Msg:
        def __init__(self, content):
            self.content = content

    class Choice:
        def __init__(self, content):
            self.message = Msg(content)

    class Resp:
        def __init__(self, content):
            self.choices = [Choice(content)]

    class FakeLLM:
        def __init__(self):
            class completions:
                @staticmethod
                def create(**kwargs):
                    captured["prompt"] = kwargs["messages"][-1]["content"]
                    return Resp("结合知识库：TP53 相关通路在结果中富集。")

            self.chat = type("C", (), {"completions": completions})()

    ex = ResultExplainer(llm_client=FakeLLM(), knowledge_client=FakeKB())
    out = ex.generate_llm_explanation(
        {"significant_genes": 20, "top_pathways": ["p53 signaling"]},
        "解释结果",
    )
    assert "知识库" in out
    assert "肿瘤抑制" in captured.get("prompt", "") + out
    assert "p53" in captured["q"]


def test_llm_failure_falls_back_to_template():
    class Boom:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise RuntimeError("api down")

    ex = ResultExplainer(llm_client=Boom(), knowledge_client=None)
    out = ex.generate_llm_explanation({"total_genes": 50, "significant_genes": 5}, "q")
    assert "待实现" not in out
    assert "5" in out


def test_llm_empty_response_falls_back_to_template():
    class EmptyResp:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    class R:
                        choices = [  # noqa: RUF012 - 测试桩，无需 ClassVar
                            type(
                                "C", (), {"message": type("M", (), {"content": "  "})()}
                            )()
                        ]

                    return R()

    ex = ResultExplainer(llm_client=EmptyResp(), knowledge_client=None)
    out = ex.generate_llm_explanation({"total_genes": 50, "significant_genes": 5}, "q")
    assert "待实现" not in out
    assert "5" in out


def test_result_explainer_accepts_model():
    from src.analysis.result_explainer import ResultExplainer

    exp = ResultExplainer(llm_client=None, knowledge_client=None, model="glm-4-Flash")
    assert exp.model == "glm-4-Flash"
    text = exp.generate_llm_explanation(
        {"total_genes": 100, "significant_genes": 10},
        question="q",
    )
    assert "总基因数：100" in text
    assert "显著差异基因（调整后p值 < 0.05）：10" in text
