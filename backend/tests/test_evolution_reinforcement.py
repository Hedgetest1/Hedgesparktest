from unittest.mock import Mock, patch
from datetime import datetime, timezone, timedelta
import pytest
from app.services.evolution_reinforcement import (
    compute_reinforcement_weights,
    get_retired_domains,
    exploration_required,
    reinforcement_multiplier,
    format_for_opus_prompt
)


def test_compute_reinforcement_weights():
    """Test basic reinforcement weights computation."""
    mock_db = Mock()
    mock_query = Mock()
    mock_db.query.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.all.return_value = []
    
    with patch('app.services.evolution_reinforcement._now') as mock_now:
        mock_now.return_value = datetime(2024, 1, 15, tzinfo=timezone.utc)
        weights = compute_reinforcement_weights(mock_db, days=7)
        
    assert isinstance(weights, dict)
    mock_db.query.assert_called_once()


def test_get_retired_domains_empty_weights():
    """Test retired domains detection with empty weights."""
    weights = {}
    retired = get_retired_domains(weights)
    
    assert isinstance(retired, list)
    assert len(retired) == 0


def test_get_retired_domains_with_data():
    """Test retired domains detection with sample data."""
    weights = {
        'domain_a': {'success_rate': 0.1, 'total_attempts': 100},
        'domain_b': {'success_rate': 0.8, 'total_attempts': 50},
        'domain_c': {'success_rate': 0.0, 'total_attempts': 200}
    }
    
    retired = get_retired_domains(weights)
    
    assert isinstance(retired, list)
    # Should identify domains with very low success rates
    domain_names = [d.get('domain') for d in retired if 'domain' in d]
    assert any('domain_a' in str(d) or 'domain_c' in str(d) for d in retired)


def test_exploration_required_empty_weights():
    """Test exploration requirement with empty weights."""
    weights = {}
    required, reason = exploration_required(weights)
    
    assert isinstance(required, bool)
    assert reason is None or isinstance(reason, str)


def test_exploration_required_with_weights():
    """Test exploration requirement with sample weights."""
    weights = {
        'domain_a': {'success_rate': 0.9, 'total_attempts': 10},
        'domain_b': {'success_rate': 0.1, 'total_attempts': 5}
    }
    
    required, reason = exploration_required(weights)
    
    assert isinstance(required, bool)
    if reason is not None:
        assert isinstance(reason, str)


def test_reinforcement_multiplier_unknown_domain():
    """Test reinforcement multiplier for unknown domain."""
    weights = {'known_domain': {'success_rate': 0.8}}
    multiplier = reinforcement_multiplier('unknown_domain', weights)
    
    assert isinstance(multiplier, float)
    assert multiplier >= 0.0


def test_reinforcement_multiplier_known_domain():
    """Test reinforcement multiplier for known domain."""
    weights = {
        'test_domain': {'success_rate': 0.7, 'total_attempts': 50}
    }
    
    multiplier = reinforcement_multiplier('test_domain', weights)
    
    assert isinstance(multiplier, float)
    assert multiplier >= 0.0


def test_format_for_opus_prompt():
    """Test formatting weights for Opus prompt."""
    weights = {
        'domain_a': {'success_rate': 0.8, 'total_attempts': 100},
        'domain_b': {'success_rate': 0.3, 'total_attempts': 20}
    }
    
    formatted = format_for_opus_prompt(weights)
    
    assert isinstance(formatted, str)
    assert len(formatted) > 0
    # Should contain domain information
    assert 'domain_a' in formatted or 'domain_b' in formatted
