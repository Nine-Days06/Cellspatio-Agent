"""CellSpatio 更名与品牌提示词断言"""


def test_main_exports_cellspatio_agent():
    import src.main as m

    assert hasattr(m, "CellSpatioAgent")
    assert not hasattr(m, "MultiomicsAgent")


def test_package_docstring_renamed():
    import src

    assert "CellSpatio" in (src.__doc__ or "") or "单细胞" in (src.__doc__ or "")
    assert "多组学分析智能体" not in (src.__doc__ or "")


def test_intent_prompt_brand():
    from src.control.intent_parser import IntentParser

    p = IntentParser()
    assert "单细胞" in p.system_prompt or "时空" in p.system_prompt
    assert "多组学分析智能体的意图识别器" not in p.system_prompt


def test_logger_default_filename():
    import inspect

    from src.logger import setup_root_logger

    sig = inspect.signature(setup_root_logger)
    assert "cellspatio" in str(sig.parameters["log_file"].default)