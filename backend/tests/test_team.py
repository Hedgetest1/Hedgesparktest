"""Tests for F9 — team collaboration (members)."""
from __future__ import annotations

import uuid

import pytest

from app.services.team import (
    add_member,
    list_members,
    remove_member,
    update_member_role,
)


def _unique_shop(prefix: str) -> str:
    """Build a unique shop domain per test run so Redis state from prior
    runs never collides with this run's writes."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}.myshopify.com"


# ---- Members ----

def test_add_and_list_member():
    shop = _unique_shop("team-crud")
    m = add_member(shop, email="dev@team.com", display_name="Dev", role="editor")
    if m is None:
        pytest.skip("redis unavailable")

    members = list_members(shop)
    assert any(mm.email == "dev@team.com" for mm in members)
    found = next(mm for mm in members if mm.email == "dev@team.com")
    assert found.role == "editor"


def test_add_member_rejects_invalid_role():
    with pytest.raises(ValueError):
        add_member(_unique_shop("bad-role"), email="x@y.com", role="king")


def test_add_member_rejects_bad_email():
    with pytest.raises(ValueError):
        add_member(_unique_shop("bad-email"), email="not-an-email", role="viewer")


def test_add_member_rejects_duplicate():
    shop = _unique_shop("dup-check")
    m = add_member(shop, email="same@team.com", role="viewer")
    if m is None:
        pytest.skip("redis unavailable")
    with pytest.raises(ValueError):
        add_member(shop, email="same@team.com", role="editor")


def test_remove_member():
    shop = _unique_shop("remove-check")
    m = add_member(shop, email="gone@team.com", role="viewer")
    if m is None:
        pytest.skip("redis unavailable")
    assert remove_member(shop, m.id) is True
    assert remove_member(shop, m.id) is False


def test_update_member_role():
    shop = _unique_shop("role-change")
    m = add_member(shop, email="grow@team.com", role="viewer")
    if m is None:
        pytest.skip("redis unavailable")
    assert update_member_role(shop, m.id, "admin") is True
    members = list_members(shop)
    found = next(mm for mm in members if mm.id == m.id)
    assert found.role == "admin"
