from src.main import CellSpatioAgent


def test_agent_initialization():
    """Test agent initialization"""
    agent = CellSpatioAgent()
    assert agent is not None
    assert hasattr(agent, 'run')

def test_workflow_execution():
    """Test basic workflow execution"""
    agent = CellSpatioAgent()
    result = agent.execute_workflow("显示系统状态")
    assert result['status'] == 'success'
