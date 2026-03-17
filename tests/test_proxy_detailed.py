"""Tests for proxy service."""
import pytest
from unittest.mock import MagicMock
from app.services.proxy_service import ProxyService


def test_proxy_service_init():
    """Test ProxyService initialization."""
    service = ProxyService()
    assert service is not None


def test_proxy_service_build_url():
    """Test building URL returns valid URL."""
    service = ProxyService()
    
    mock_agent = MagicMock()
    mock_agent.service_name = "test-agent"
    mock_agent.namespace = "test-ns"
    
    url = service.build_agent_base_url(mock_agent)
    
    # Should return valid URL
    assert url.startswith("http")


def test_proxy_service_has_forward():
    """Test service has forward method."""
    service = ProxyService()
    assert hasattr(service, 'forward')
