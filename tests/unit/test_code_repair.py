"""CodeRepairer 单测"""
from src.analysis.code_repair import CodeRepairer


def test_repair_returns_new_code_from_llm():
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
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return Resp("# fixed script")

    r = CodeRepairer(llm_client=FakeLLM())
    out = r.repair("# bad", "object not found")
    assert out == "# fixed script"


def test_repair_without_llm_returns_none():
    assert CodeRepairer(llm_client=None).repair("# bad", "err") is None


def test_repair_llm_failure_returns_none():
    class Boom:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise RuntimeError("x")
    assert CodeRepairer(llm_client=Boom()).repair("# bad", "err") is None
