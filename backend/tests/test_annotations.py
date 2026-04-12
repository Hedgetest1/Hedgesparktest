"""Tests for F5 — chart annotations CRUD."""
from __future__ import annotations

import pytest

from app.services.annotations import (
    create_annotation,
    delete_annotation,
    get_annotations_in_range,
    list_annotations,
)


def test_create_and_list_annotation():
    shop = "ann-crud-shop.myshopify.com"
    ann = create_annotation(
        shop,
        date="2026-03-15",
        label="Launched FB campaign",
        description="Retargeting, €500/day budget",
        category="campaign",
    )
    if ann is None:
        pytest.skip("redis unavailable")

    listed = list_annotations(shop)
    assert any(a.label == "Launched FB campaign" for a in listed)


def test_create_rejects_invalid_category():
    with pytest.raises(ValueError):
        create_annotation(
            "any-shop.myshopify.com",
            date="2026-03-15",
            label="X",
            category="bogus",
        )


def test_create_rejects_bad_date_format():
    with pytest.raises(ValueError):
        create_annotation(
            "any-shop.myshopify.com",
            date="not-a-date",
            label="X",
            category="other",
        )


def test_create_rejects_empty_label():
    with pytest.raises(ValueError):
        create_annotation(
            "any-shop.myshopify.com",
            date="2026-03-15",
            label="   ",
            category="other",
        )


def test_delete_annotation():
    shop = "ann-delete-shop.myshopify.com"
    ann = create_annotation(
        shop,
        date="2026-02-01",
        label="Price increase",
        category="pricing",
    )
    if ann is None:
        pytest.skip("redis unavailable")

    removed = delete_annotation(shop, ann.id)
    assert removed is True

    # Second delete returns False
    assert delete_annotation(shop, ann.id) is False


def test_get_in_range_filters_correctly():
    shop = "ann-range-shop.myshopify.com"
    a1 = create_annotation(shop, date="2026-01-10", label="A", category="other")
    a2 = create_annotation(shop, date="2026-02-15", label="B", category="other")
    a3 = create_annotation(shop, date="2026-03-20", label="C", category="other")
    if a1 is None:
        pytest.skip("redis unavailable")

    in_range = get_annotations_in_range(shop, "2026-02-01", "2026-02-28")
    labels = {a.label for a in in_range}
    assert "B" in labels
    assert "A" not in labels
    assert "C" not in labels
