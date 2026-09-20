from src.main import MultiomicsAgent


def test_agent_initialization():
    """Test agent initialization"""
    agent = MultiomicsAgent()
    assert agent is not None
    assert hasattr(agent, 'run')

def test_workflow_execution():
    """Test basic workflow execution"""
    agent = MultiomicsAgent()
    result = agent.execute_workflow("显示系统状态")
    assert result['status'] == 'success'
