"""Tests for F9 — team collaboration + H1 @mention notifications."""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from app.services.team import (
    add_comment,
    add_member,
    delete_comment,
    extract_mentions,
    list_comments,
    list_members,
    remove_member,
    update_member_role,
)


def _unique_shop(prefix: str) -> str:
    """Build a unique shop domain per test run so Redis state from prior
    runs never collides with this run's writes."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}.myshopify.com"


# ---- Pure helpers ----

def test_extract_mentions():
    body = "Hey @alice and @bob.smith please review this, ping @carol"
    mentions = extract_mentions(body)
    assert "alice" in mentions
    assert "bob.smith" in mentions
    assert "carol" in mentions


def test_extract_mentions_empty():
    assert extract_mentions("no mentions here") == []


def test_extract_mentions_dedup():
    mentions = extract_mentions("@alice said hi to @alice")
    assert mentions == ["alice"]


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


# ---- Comments ----

def test_add_and_list_comment():
    shop = _unique_shop("comments-crud")
    c = add_comment(
        shop,
        entity_type="bugfix_candidate",
        entity_id="42",
        author_id="owner",
        author_name="Owner",
        body="@alice please review this patch",
    )
    if c is None:
        pytest.skip("redis unavailable")

    comments = list_comments(shop, "bugfix_candidate", "42")
    assert any(cc.id == c.id for cc in comments)
    assert "alice" in comments[0].mentions


def test_add_comment_rejects_empty():
    with pytest.raises(ValueError):
        add_comment(
            _unique_shop("empty-comment"),
            entity_type="test", entity_id="1",
            author_id="o", author_name="O", body="   ",
        )


def test_delete_comment():
    shop = _unique_shop("comment-delete")
    c = add_comment(
        shop,
        entity_type="finding", entity_id="99",
        author_id="o", author_name="Owner", body="Temporary note",
    )
    if c is None:
        pytest.skip("redis unavailable")
    assert delete_comment(shop, "finding", "99", c.id) is True
    assert delete_comment(shop, "finding", "99", c.id) is False


def test_comments_scoped_per_entity():
    shop = _unique_shop("scope-check")
    a = add_comment(shop, entity_type="goal", entity_id="A",
                     author_id="o", author_name="Owner", body="first")
    b = add_comment(shop, entity_type="goal", entity_id="B",
                     author_id="o", author_name="Owner", body="second")
    if a is None or b is None:
        pytest.skip("redis unavailable")

    comments_a = list_comments(shop, "goal", "A")
    comments_b = list_comments(shop, "goal", "B")
    assert any(c.id == a.id for c in comments_a)
    assert not any(c.id == b.id for c in comments_a)
    assert any(c.id == b.id for c in comments_b)


# ---- H1 — Mention resolution + notifications ----

def test_resolve_mentions_matches_display_name():
    """@alice matches a team member whose display_name is 'alice'."""
    from app.services.team import _resolve_mentions_to_members
    shop = _unique_shop("mention-match")
    m = add_member(shop, email="alice@team.com", display_name="alice", role="editor")
    if m is None:
        pytest.skip("redis unavailable")
    matched = _resolve_mentions_to_members(shop, ["alice"])
    assert len(matched) == 1
    assert matched[0].email == "alice@team.com"


def test_resolve_mentions_matches_email_local_part():
    """@bob matches bob@anywhere.com by the email local part."""
    from app.services.team import _resolve_mentions_to_members
    shop = _unique_shop("mention-email")
    m = add_member(shop, email="bob@elsewhere.com", display_name="Robert", role="viewer")
    if m is None:
        pytest.skip("redis unavailable")
    matched = _resolve_mentions_to_members(shop, ["bob"])
    assert len(matched) == 1
    assert matched[0].email == "bob@elsewhere.com"


def test_resolve_mentions_ignores_unknowns():
    """@unknown_handle returns no match in an empty team."""
    from app.services.team import _resolve_mentions_to_members
    shop = _unique_shop("mention-empty")
    matched = _resolve_mentions_to_members(shop, ["ghost", "phantom"])
    assert matched == []


def test_add_comment_triggers_mention_notification(monkeypatch):
    """A comment with @member fires an email intent via the orchestrator."""
    shop = _unique_shop("mention-email-test")
    m = add_member(shop, email="carol@team.com", display_name="carol", role="admin")
    if m is None:
        pytest.skip("redis unavailable")

    sent_intents = []
    def _fake_send(db, intent):
        sent_intents.append(intent)
        return {"status": "sent", "reason": None, "resend_id": "fake_id"}

    # Patch at the team module level since that's what add_comment imports
    monkeypatch.setattr(
        "app.services.email_orchestrator.send_immediate",
        _fake_send,
    )

    c = add_comment(
        shop,
        entity_type="finding", entity_id="123",
        author_id="owner", author_name="Shop Owner",
        body="@carol please take a look at this loss pattern",
    )
    assert c is not None
    assert "carol" in c.mentions
    assert len(sent_intents) == 1
    assert sent_intents[0].to_email == "carol@team.com"
    assert sent_intents[0].email_type == "team_mention"


def test_add_comment_without_mentions_sends_no_email(monkeypatch):
    """A comment with no @ syntax must not send any notifications."""
    call_count = [0]
    def _fake_send(db, intent):
        call_count[0] += 1
        return {"status": "sent"}

    monkeypatch.setattr(
        "app.services.email_orchestrator.send_immediate",
        _fake_send,
    )

    shop = _unique_shop("no-mention-shop")
    c = add_comment(
        shop,
        entity_type="goal", entity_id="X",
        author_id="o", author_name="O", body="just a plain note",
    )
    if c is None:
        pytest.skip("redis unavailable")
    assert c.mentions == []
    assert call_count[0] == 0
