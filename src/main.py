import logging
from typing import Any

from src.analysis.r_executor import RExecutor
from src.analysis.visualization import Visualizer
from src.control.intent_parser import IntentParser
from src.control.r_script_generator import RScriptGenerator
from src.control.workflow_manager import WorkflowManager
from src.data.registry import FetcherRegistry
from src.data.storage import FetcherStorage
from src.knowledge.knowledge_builder import KnowledgeBuilder
from src.knowledge.lightrag_client import LightRAGClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MultiomicsAgent:
    """人类多组学分析智能体主类"""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

        # 初始化各个组件
        self.intent_parser = IntentParser()

        llm_cfg = self.config.get("llm", {})
        provider = llm_cfg.get("provider")
        lightrag_config = (
            {"provider": provider} if provider not in (None, "mock") else {}
        )

        self.knowledge_client = LightRAGClient(
            working_dir=self.config.get("knowledge_dir", "./knowledge_base"),
            config=lightrag_config,
        )
        self.r_executor = RExecutor()
        self.visualizer = Visualizer()
        self.r_script_generator = RScriptGenerator()

        from src.knowledge.methods_kb import MethodsKb

        self.methods_kb = MethodsKb(
            client=LightRAGClient(
                working_dir=self.config.get(
                    "methods_knowledge_dir", "./knowledge_base_methods"
                ),
                config=lightrag_config,
            ),
        )

        self.storage = FetcherStorage(base_dir=self.config.get("data_dir", "data/raw"))
        self.fetcher_registry = FetcherRegistry.build_default(storage=self.storage)
        self.knowledge_builder = KnowledgeBuilder(
            self.knowledge_client, fetcher_registry=self.fetcher_registry
        )

        from src.analysis.result_explainer import ResultExplainer

        self.result_explainer = ResultExplainer(knowledge_client=self.knowledge_client)

        self.workflow_manager = WorkflowManager(
            intent_parser=self.intent_parser,
            knowledge_client=self.knowledge_client,
            r_executor=self.r_executor,
            visualizer=self.visualizer,
            fetcher_registry=self.fetcher_registry,
            storage=self.storage,
            knowledge_builder=self.knowledge_builder,
            r_script_generator=self.r_script_generator,
            methods_kb=self.methods_kb,
            explainer=self.result_explainer,
        )

        logger.info("MultiomicsAgent initialized")

    def execute_workflow(
        self, user_input: str, context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """执行工作流"""
        return self.workflow_manager.execute_workflow(user_input, context)

    def confirm_and_download(self, source: str, asset_id: str) -> dict[str, Any]:
        """确认并下载数据资产（UI/CLI 供用户在候选选择后调用）"""
        return self.workflow_manager.confirm_and_download(source, asset_id)

    def ingest_asset(self, source: str, asset_id: str) -> dict[str, Any]:
        """将资产写入知识库（知识流）"""
        return self.workflow_manager.ingest_asset_to_kb(source, asset_id)

    def run(self, mode: str = "cli"):
        """运行智能体"""
        if mode == "cli":
            self._run_cli()
        elif mode == "web":
            self._run_web()
        else:
            raise ValueError(f"Unsupported mode: {mode}")

    def _run_cli(self):
        """命令行模式"""
        print("人类多组学分析智能体已启动（CLI模式）")
        print("输入 'quit' 或 'exit' 退出\n")

        while True:
            try:
                user_input = input("用户: ").strip()
                if user_input.lower() in ["quit", "exit"]:
                    break

                result = self.execute_workflow(user_input)
                print(
                    f"智能体: {result.get('message', result.get('response', '无响应'))}\n"
                )

            except KeyboardInterrupt:
                break
            except Exception as e:  # noqa: BLE001 - CLI 交互循环需兜底所有异常
                print(f"错误: {e}\n")

        print("感谢使用，再见！")

    def _run_web(self):
        """Web 界面模式 - 提示用户使用 streamlit run 启动"""
        import subprocess
        import sys

        print("启动 Streamlit Web 界面...")
        subprocess.run(
            [sys.executable, "-m", "streamlit", "run", "src/ui/app.py"], check=False
        )


if __name__ == "__main__":
    agent = MultiomicsAgent()
    agent.run("cli")
