"""评测适配器覆盖生产 AgentRuntime 实际调用的 workflow 接口。"""

from evals.run_eval import _RecordingWM


def test_recording_workflow_supports_memory_query() -> None:
    """query_memory 工具不再因适配器缺方法而崩溃。"""
    wm = _RecordingWM()

    result = wm.query_memory_for_agent("上次差异表达用了什么参数", {"history": []})

    assert wm.last == ("query_memory", {"query": "上次差异表达用了什么参数"})
    assert (result["status"], result["type"]) == ("success", "memory_response")
