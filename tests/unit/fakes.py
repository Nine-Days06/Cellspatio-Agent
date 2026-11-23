"""共享测试桩：FakeIntent、FakeGen、FakeExec 与固定 QC 证据执行器。

合并原则：保留最完整实现，通过构造参数覆盖行为差异。
若某测试需极特殊行为（如 FlakyExec 计数失败、写文件），
可在测试内部子类化或实例化时传入可调用对象覆盖。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

_OUTPUT_DECL = re.compile(r'^output_file\s*<-\s*"(.+?)"\s*$', re.MULTILINE)


def write_declared_output(code: str, text: str = "gene,padj\nG1,0.01\n") -> None:
    """按脚本里声明的 `output_file <- "..."` 落一个非空产物，模拟真实 R 脚本写出结果。"""
    for raw in _OUTPUT_DECL.findall(code or ""):
        path = Path(raw.replace("\\\\", "\\"))
        if not path.suffix:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


class RecordingKGMemory:
    """记录执行事实的 KGMemory 测试替身；查询不访问外部服务。"""

    def __init__(self) -> None:
        self.records: list[Any] = []

    def ingest(self, execution: Any) -> None:
        self.records.append(execution)

    def query_entities(self, query: str) -> list[str]:
        return []


class FakeIntent:
    """意图解析器桩。

    默认返回 analysis/differential_expression 意图。
    可通过构造参数自定义 parse 返回值与 extract_parameters 返回值。
    """

    def __init__(
        self,
        *,
        parse_return: dict[str, Any] | None = None,
        extract_params_return: dict[str, Any] | None = None,
        track_context: bool = False,
    ):
        self._parse_return = parse_return or {
            "type": "analysis",
            "analysis_type": "differential_expression",
            "original_input": "",
        }
        self._extract_params_return = extract_params_return or {
            "input_files": [str(Path(__file__).parent / "fixtures" / "counts.csv")]
        }
        self.track_context = track_context
        self.seen_context: Any = None

    def parse(self, user_input: str, context: Any = None) -> dict[str, Any]:
        if self.track_context:
            self.seen_context = context
        # 允许动态注入 original_input
        ret = dict(self._parse_return)
        ret["original_input"] = user_input
        return ret

    def extract_parameters(self, user_input: str) -> dict[str, Any]:
        return dict(self._extract_params_return)


class FakeGen:
    """R 脚本生成器桩。

    默认返回简单字符串。可选记录调用历史。
    """

    def __init__(
        self,
        *,
        return_value: str = "# script",
        track_calls: bool = False,
        track_ctx: bool = False,
    ):
        self._return_value = return_value
        self.track_calls = track_calls
        self.track_ctx = track_ctx
        self.calls: list[dict[str, Any]] = []
        self.ctx_seen: Any = "unset"
        self.last: dict[str, Any] | None = None

    def generate_code(
        self, analysis_type: str, params: dict[str, Any], method_context: str | None = None
    ) -> str:
        if self.track_calls:
            self.calls.append({"type": analysis_type, "ctx": method_context})
        if self.track_ctx:
            self.ctx_seen = method_context
        self.last = {"type": analysis_type, "params": params}
        output = params.get("output_file")
        if not output:
            return self._return_value
        # 真实脚本会写 output_file；桩也声明一行，供 FakeExec 落盘
        return f'{self._return_value}\noutput_file <- "{output}"\n'


class FakeExec:
    """R 执行器桩。

    默返回 returncode=0 的 SimpleNamespace。
    可选：
    - track_codes: 记录执行的代码列表
    - fail_once: 第一次调用抛出异常，后续成功
    - side_effect: 可调用对象，接收 code 返回自定义结果或抛异常
    默认按脚本里声明的 output_file 落一个非空产物；outputs=False 可关闭，
    用于覆盖“脚本正常退出但没有产物”的失败路径。
    """

    def __init__(
        self,
        *,
        track_codes: bool = False,
        fail_once: bool = False,
        side_effect: Callable[[str], Any] | None = None,
        return_value: Any | None = None,
        outputs: bool = True,
        output_text: str = "gene,padj\nG1,0.01\n",
    ):
        self.track_codes = track_codes
        self.fail_once = fail_once
        self._fail_count = 0
        self.side_effect = side_effect
        self._return_value = return_value
        self.outputs = outputs
        self.output_text = output_text
        self.codes: list[str] = []

    def execute_code(self, code: str) -> Any:
        if self.track_codes:
            self.codes.append(code)

        if self.side_effect is not None:
            return self.side_effect(code)

        if self.fail_once:
            self._fail_count += 1
            if self._fail_count == 1:
                raise RuntimeError("first fail")

        if self.outputs:
            write_declared_output(code, self.output_text)

        if self._return_value is not None:
            return self._return_value

        # 默认返回成功的 R 结果对象
        class R:
            returncode = 0

        return R()


class FakeRuntime:
    """AgentRuntime 桩：记录 execute 调用参数，用 on_event 预置发射后回放结果。

    results 队列按顺序回放；events 中任一事件让 on_event 返回 False 时，
    桩立刻返回 aborted，模拟协作式中止。
    """

    def __init__(self, results=None, events=None, default=None):
        self.results = list(results or [])
        self.events = list(events or [])
        self.default = default or {
            "status": "success",
            "type": "general_response",
            "message": "回答",
        }
        self.calls: list[dict[str, Any]] = []

    def execute(self, user_input, context=None, on_event=None):
        self.calls.append({"user_input": user_input, "context": context})
        if on_event is not None:
            for event in self.events:
                if not on_event(event):
                    return {"status": "aborted", "type": "general_response", "message": "已中断"}
        return self.results.pop(0) if self.results else self.default


class FakeAgent:
    """CellSpatioAgent 桩：只暴露 chat_service 与 confirm 端点用到的面。"""

    def __init__(
        self,
        runtime=None,
        confirm_result=None,
        download_result=None,
        llm_client=None,
        llm_model="fake-model",
    ):
        self.agent_runtime = runtime if runtime is not None else FakeRuntime()
        self.confirm_result = confirm_result
        self.download_result = download_result
        self.llm_client = llm_client
        self.llm_model = llm_model
        self.config: dict[str, Any] = {}
        self.confirm_calls: list[dict[str, Any]] = []
        self.download_calls: list[dict[str, Any]] = []

    def execute_confirmed_script(
        self, analysis_type, params, script, method_context=None, session_id=None
    ):
        self.confirm_calls.append(
            {
                "analysis_type": analysis_type,
                "params": params,
                "script": script,
                "method_context": method_context,
                "session_id": session_id,
            }
        )
        return self.confirm_result or {"status": "success", "message": "分析完成"}

    def confirm_and_download(self, source, asset_id, query=""):
        self.download_calls.append({"source": source, "asset_id": asset_id, "query": query})
        return self.download_result or {}


QC_HEADER = b"barcode,nCount_RNA,nFeature_RNA,percent_mt,missing_reason\n"


def fake_qc_executor(features_csv: str, cell_qc_csv: bytes) -> Mock:
    """固定 QC 测试执行器：只写证据文件，不启动 R。"""

    def execute(script):
        directory = Path(script).parent
        (directory / "features.csv").write_text(features_csv, encoding="utf-8")
        (directory / "cell_qc.csv").write_bytes(cell_qc_csv)
        (directory / "versions.txt").write_text(
            "R 4.6.1\nSeurat 5.6.0\nMatrix 1.7.5\n", encoding="utf-8"
        )
        (directory / "qc.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    executor = Mock()
    executor.execute_script.side_effect = execute
    return executor
