"""Tests for repositories - basic existence tests."""
import pytest
from unittest.mock import MagicMock
from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_identity_binding_repo import AgentIdentityBindingRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.repositories.user_repo import UserRepository
from app.repositories.audit_repo import AuditRepository
from app.repositories.capability_profile_repo import CapabilityProfileRepository
from app.repositories.external_event_subscription_repo import ExternalEventSubscriptionRepository
from app.repositories.policy_profile_repo import PolicyProfileRepository


def test_agent_repo_init():
    """Test AgentRepository initialization."""
    mock_db = MagicMock()
    repo = AgentRepository(mock_db)
    assert repo.db == mock_db


def test_agent_repo_create():
    """Test AgentRepository.create method exists and is callable."""
    mock_db = MagicMock()
    repo = AgentRepository(mock_db)
    assert hasattr(repo, 'create')
    assert callable(repo.create)


def test_agent_repo_list_by_owner():
    """Test AgentRepository.list_by_owner exists."""
    mock_db = MagicMock()
    repo = AgentRepository(mock_db)
    assert hasattr(repo, 'list_by_owner')


def test_agent_repo_list_public():
    """Test AgentRepository.list_public exists."""
    mock_db = MagicMock()
    repo = AgentRepository(mock_db)
    assert hasattr(repo, 'list_public')


def test_agent_repo_list_all():
    """Test AgentRepository.list_all exists."""
    mock_db = MagicMock()
    repo = AgentRepository(mock_db)
    assert hasattr(repo, 'list_all')


def test_agent_repo_get_by_id():
    """Test AgentRepository.get_by_id exists."""
    mock_db = MagicMock()
    repo = AgentRepository(mock_db)
    assert hasattr(repo, 'get_by_id')


def test_agent_repo_save():
    """Test AgentRepository.save exists."""
    mock_db = MagicMock()
    repo = AgentRepository(mock_db)
    assert hasattr(repo, 'save')


def test_agent_repo_delete():
    """Test AgentRepository.delete exists."""
    mock_db = MagicMock()
    repo = AgentRepository(mock_db)
    assert hasattr(repo, 'delete')


def test_user_repo_init():
    """Test UserRepository initialization."""
    mock_db = MagicMock()
    repo = UserRepository(mock_db)
    assert repo.db == mock_db


def test_user_repo_get_by_username():
    """Test UserRepository.get_by_username exists."""
    mock_db = MagicMock()
    repo = UserRepository(mock_db)
    assert hasattr(repo, 'get_by_username')


def test_user_repo_get_by_id():
    """Test UserRepository.get_by_id exists."""
    mock_db = MagicMock()
    repo = UserRepository(mock_db)
    assert hasattr(repo, 'get_by_id')


def test_user_repo_create():
    """Test UserRepository.create exists."""
    mock_db = MagicMock()
    repo = UserRepository(mock_db)
    assert hasattr(repo, 'create')


def test_audit_repo_init():
    """Test AuditRepository initialization."""
    mock_db = MagicMock()
    repo = AuditRepository(mock_db)
    assert repo.db == mock_db


def test_audit_repo_create():
    """Test AuditRepository.create exists."""
    mock_db = MagicMock()
    repo = AuditRepository(mock_db)
    assert hasattr(repo, 'create')


def test_capability_profile_repo_methods():
    mock_db = MagicMock()
    repo = CapabilityProfileRepository(mock_db)
    assert hasattr(repo, "create")
    assert hasattr(repo, "get_by_id")
    assert hasattr(repo, "list_all")
    assert hasattr(repo, "save")
    assert hasattr(repo, "delete")


def test_policy_profile_repo_methods():
    mock_db = MagicMock()
    repo = PolicyProfileRepository(mock_db)
    assert hasattr(repo, "create")
    assert hasattr(repo, "get_by_id")
    assert hasattr(repo, "list_all")
    assert hasattr(repo, "save")
    assert hasattr(repo, "delete")


def test_agent_identity_binding_repo_methods():
    mock_db = MagicMock()
    repo = AgentIdentityBindingRepository(mock_db)
    assert hasattr(repo, "create")
    assert hasattr(repo, "get_by_id")
    assert hasattr(repo, "list_by_agent")
    assert hasattr(repo, "list_enabled_bindings_for_agent")
    assert hasattr(repo, "find_binding")
    assert hasattr(repo, "get_by_agent_and_binding_key")
    assert hasattr(repo, "save")
    assert hasattr(repo, "delete")


def test_external_event_subscription_repo_methods():
    mock_db = MagicMock()
    repo = ExternalEventSubscriptionRepository(mock_db)
    assert hasattr(repo, "create")
    assert hasattr(repo, "get_by_id")
    assert hasattr(repo, "list_all")
    assert hasattr(repo, "list_by_agent")
    assert hasattr(repo, "list_enabled_for_source")
    assert hasattr(repo, "save")
    assert hasattr(repo, "delete")


def test_agent_task_repo_methods():
    mock_db = MagicMock()
    repo = AgentTaskRepository(mock_db)
    assert hasattr(repo, "create")
    assert hasattr(repo, "get_by_id")
    assert hasattr(repo, "list_all")
    assert hasattr(repo, "list_by_agent")
    assert hasattr(repo, "find_recent_duplicate")
    assert hasattr(repo, "save")
    assert hasattr(repo, "delete")
