"""共享测试桩：FakeIntent、FakeGen、FakeExec 的统一定义。

合并原则：保留最完整实现，通过构造参数覆盖行为差异。
若某测试需极特殊行为（如 FlakyExec 计数失败、写文件），
可在测试内部子类化或实例化时传入可调用对象覆盖。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable


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
        self._extract_params_return = extract_params_return or {"input_files": ["counts.csv"]}
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
        return self._return_value


class FakeExec:
    """R 执行器桩。

    默返回 returncode=0 的 SimpleNamespace。
    可选：
    - track_codes: 记录执行的代码列表
    - fail_once: 第一次调用抛出异常，后续成功
    - side_effect: 可调用对象，接收 code 返回自定义结果或抛异常
    """

    def __init__(
        self,
        *,
        track_codes: bool = False,
        fail_once: bool = False,
        side_effect: Callable[[str], Any] | None = None,
        return_value: Any | None = None,
    ):
        self.track_codes = track_codes
        self.fail_once = fail_once
        self._fail_count = 0
        self.side_effect = side_effect
        self._return_value = return_value
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

        if self._return_value is not None:
            return self._return_value

        # 默认返回成功的 R 结果对象
        class R:
            returncode = 0

        return R()