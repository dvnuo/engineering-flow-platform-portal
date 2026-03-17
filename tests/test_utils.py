"""Tests for utils."""
from app.utils.naming import to_k8s_name, runtime_names


def test_to_k8s_name():
    """Test K8s name conversion."""
    # Test normal name - gets prefixed with "agent-"
    result = to_k8s_name("my-agent")
    assert "my-agent" in result
    assert result.startswith("agent-")
    
    # Test name with spaces
    result = to_k8s_name("my agent")
    assert " " not in result
    
    # Test long name truncation
    long_name = "a" * 100
    result = to_k8s_name(long_name)
    assert len(result) <= 63  # K8s name max length


def test_runtime_names():
    """Test runtime name generation."""
    result = runtime_names("my-agent-123")
    assert isinstance(result, tuple)
    assert len(result) == 4


def test_to_k8s_name_special_chars():
    """Test handling of special characters."""
    # Test underscore - should be replaced with dash
    result = to_k8s_name("my_agent")
    assert "_" not in result
    assert "-" in result
    
    # Test @ special char
    result = to_k8s_name("agent@123")
    assert "@" not in result
    assert result.startswith("agent-")


def test_to_k8s_name_with_prefix():
    """Test name with prefix."""
    result = to_k8s_name("my-agent", prefix="test")
    assert "test" in result.lower()
