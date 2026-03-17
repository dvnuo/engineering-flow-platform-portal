"""Tests for K8s service - noop mode."""
import pytest
from unittest.mock import MagicMock
from app.services.k8s_service import K8sService, RuntimeStatus


def test_runtime_status():
    """Test RuntimeStatus structure."""
    status = RuntimeStatus(status="running", message="OK")
    assert status.status == "running"
    assert status.message == "OK"


def test_k8s_service_init():
    """Test K8sService initialization."""
    service = K8sService()
    assert service is not None
    assert hasattr(service, 'enabled')
    assert hasattr(service, 'settings')


def test_k8s_service_noop_create_agent():
    """Test creating agent in noop mode."""
    service = K8sService()
    
    mock_agent = MagicMock()
    mock_agent.id = "test-agent"
    mock_agent.name = "Test Agent"
    mock_agent.namespace = "test-ns"
    mock_agent.service_name = "test-svc"
    
    result = service.create_agent_runtime(mock_agent)
    
    # In noop mode, returns running
    assert result.status == "running"


def test_k8s_service_noop_delete_agent():
    """Test deleting agent in noop mode."""
    service = K8sService()
    
    mock_agent = MagicMock()
    mock_agent.id = "test-agent"
    mock_agent.namespace = "test-ns"
    
    result = service.delete_agent_runtime(mock_agent)
    
    assert result.status == "deleted"


def test_k8s_service_noop_start_agent():
    """Test starting agent in noop mode."""
    service = K8sService()
    
    mock_agent = MagicMock()
    mock_agent.id = "test-agent"
    mock_agent.namespace = "test-ns"
    
    result = service.start_agent(mock_agent)
    
    assert result.status == "running"


def test_k8s_service_noop_stop_agent():
    """Test stopping agent in noop mode."""
    service = K8sService()
    
    mock_agent = MagicMock()
    mock_agent.id = "test-agent"
    mock_agent.namespace = "test-ns"
    
    result = service.stop_agent(mock_agent)
    
    assert result.status == "stopped"


def test_k8s_service_noop_get_status():
    """Test getting status in noop mode."""
    service = K8sService()
    
    mock_agent = MagicMock()
    mock_agent.id = "test-agent"
    mock_agent.namespace = "test-ns"
    
    result = service.get_agent_runtime_status(mock_agent)
    
    assert result.status is not None
