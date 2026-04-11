from unittest.mock import Mock, patch
import pytest
from app.services.revenue_triggers import (
    run_revenue_triggers,
    _find_best_trigger,
    _build_trigger_html,
    _product_name,
    _get_aov
)


def test_run_revenue_triggers_no_shops():
    """Test run_revenue_triggers when no shops are found."""
    db = Mock()
    db.execute.return_value.fetchall.return_value = []
    
    result = run_revenue_triggers(db)
    
    assert result == {"triggers_sent": 0, "shops_processed": 0}
    db.execute.assert_called_once()


def test_find_best_trigger_returns_none_when_no_data():
    """Test _find_best_trigger returns None when no trigger data found."""
    db = Mock()
    db.execute.return_value.fetchone.return_value = None
    
    result = _find_best_trigger(db, "test-shop")
    
    assert result is None
    db.execute.assert_called_once()


def test_find_best_trigger_returns_trigger_data():
    """Test _find_best_trigger returns trigger data when found."""
    db = Mock()
    mock_row = Mock()
    mock_row._asdict.return_value = {
        "product_url": "https://example.com/product",
        "revenue_impact": 150.0,
        "conversion_rate": 0.05
    }
    db.execute.return_value.fetchone.return_value = mock_row
    
    result = _find_best_trigger(db, "test-shop")
    
    assert result == {
        "product_url": "https://example.com/product",
        "revenue_impact": 150.0,
        "conversion_rate": 0.05
    }


def test_build_trigger_html_contains_key_elements():
    """Test _build_trigger_html generates HTML with expected content."""
    trigger = {
        "product_url": "https://example.com/product",
        "revenue_impact": 150.0,
        "conversion_rate": 0.05
    }
    
    html = _build_trigger_html(trigger)
    
    assert "https://example.com/product" in html
    assert "150" in html
    assert "HedgeSpark" in html
    assert "<html" in html


def test_product_name_fallback():
    """Test _product_name returns URL when no product found in database."""
    db = Mock()
    db.execute.return_value.fetchone.return_value = None
    
    result = _product_name(db, "test-shop", "https://example.com/cool-product")
    
    assert result == "https://example.com/cool-product"


def test_get_aov_fallback():
    """Test _get_aov returns default value when no orders found."""
    db = Mock()
    db.execute.return_value.fetchone.return_value = None
    
    result = _get_aov(db, "test-shop")
    
    assert result == 50.0
    db.execute.assert_called_once()

