import logging
from typing import Any

from src.analysis.r_executor import RExecutor
from src.analysis.visualization import Visualizer
from src.control.intent_parser import IntentParser
from src.control.workflow_manager import WorkflowManager
from src.knowledge.lightrag_client import LightRAGClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MultiomicsAgent:
    """人类多组学分析智能体主类"""
    
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        
        # 初始化各个组件
        self.intent_parser = IntentParser()
        self.knowledge_client = LightRAGClient(
            working_dir=self.config.get('knowledge_dir', './knowledge_base')
        )
        self.r_executor = RExecutor()
        self.visualizer = Visualizer()
        
        # 初始化工作流管理器
        self.workflow_manager = WorkflowManager(
            intent_parser=self.intent_parser,
            knowledge_client=self.knowledge_client,
            r_executor=self.r_executor,
            visualizer=self.visualizer
        )
        
        logger.info("MultiomicsAgent initialized")
    
    def execute_workflow(self, user_input: str) -> dict[str, Any]:
        """执行工作流"""
        return self.workflow_manager.execute_workflow(user_input)
    
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
                if user_input.lower() in ['quit', 'exit']:
                    break
                
                result = self.execute_workflow(user_input)
                print(f"智能体: {result.get('message', result.get('response', '无响应'))}\n")
                
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
        subprocess.run([sys.executable, "-m", "streamlit", "run", "src/ui/app.py"], check=False)
