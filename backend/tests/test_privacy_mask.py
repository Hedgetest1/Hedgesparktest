"""Tests for `app.core.privacy.mask_email`.

Contract: log-safe email masking for GDPR Art. 5(1)(f) / Art. 32.
The domain is preserved (debugging signal), the local part is
redacted after 2 chars so identity cannot be reconstructed.
"""
from __future__ import annotations

from app.core.privacy import mask_email


def test_normal_email_keeps_two_chars_and_domain():
    assert mask_email("alice@example.com") == "al***@example.com"


def test_short_local_part():
    assert mask_email("ab@x.co") == "a***@x.co"


def test_one_char_local_part():
    assert mask_email("x@y.io") == "***@y.io"


def test_none_returns_triple_star():
    assert mask_email(None) == "***"


def test_empty_string_returns_triple_star():
    assert mask_email("") == "***"


def test_non_string_returns_triple_star():
    assert mask_email(123) == "***"  # type: ignore[arg-type]
    assert mask_email([]) == "***"  # type: ignore[arg-type]


def test_missing_at_sign_returns_triple_star():
    assert mask_email("not-an-email") == "***"


def test_trailing_at_returns_triple_star():
    assert mask_email("foo@") == "***"


def test_leading_at_returns_triple_star():
    assert mask_email("@example.com") == "***"


def test_preserves_subdomain_in_output():
    assert mask_email("john.smith@mail.example.co.uk") == "jo***@mail.example.co.uk"


def test_never_raises():
    """Must be log-safe: no input is allowed to raise an exception."""
    for bad_input in [None, "", "x", "@", "@@", "a@b@c", 42, [], {}, object()]:
        try:
            result = mask_email(bad_input)  # type: ignore[arg-type]
            assert isinstance(result, str)
        except Exception as exc:
            raise AssertionError(f"mask_email raised on {bad_input!r}: {exc}")
