"""Tests for sentry_poller — closes the email-only-no-pipeline gap.

The poller is meant to feed Sentry issues into sentry_triage (and from
there into BugFixCandidate). These tests pin the behaviors that matter:
  - Test environment short-circuits (never hits the live API).
  - Missing credentials short-circuit cleanly.
  - High-volume recent issues forward to ingest_webhook.
  - Stale / low-volume issues are skipped without forwarding.
  - Cooldown prevents repeated polling within 3 minutes.
"""
from __future__ import annotations

import os

from app.services import sentry_poller


# ---------------------------------------------------------------------------
# Env / cooldown gates
# ---------------------------------------------------------------------------

def test_test_env_short_circuits(db):
    """APP_ENV=test (set by conftest) MUST cause early return."""
    result = sentry_poller.poll_recent_issues(db)
    assert result == {"status": "skipped", "reason": "test_env"}


def test_missing_credentials_short_circuit(db):
    """When APP_ENV != test but credentials are missing, return cleanly."""
    saved_env = os.environ.get("APP_ENV")
    saved_token = os.environ.pop("SENTRY_AUTH_TOKEN", None)
    saved_org = os.environ.pop("SENTRY_ORG", None)
    saved_project = os.environ.pop("SENTRY_PROJECT", None)
    os.environ["APP_ENV"] = "production"
    try:
        result = sentry_poller.poll_recent_issues(db)
        assert result == {"status": "skipped", "reason": "missing_credentials"}
    finally:
        if saved_env is not None:
            os.environ["APP_ENV"] = saved_env
        else:
            os.environ.pop("APP_ENV", None)
        if saved_token is not None:
            os.environ["SENTRY_AUTH_TOKEN"] = saved_token
        if saved_org is not None:
            os.environ["SENTRY_ORG"] = saved_org
        if saved_project is not None:
            os.environ["SENTRY_PROJECT"] = saved_project


# ---------------------------------------------------------------------------
# Forwarding logic — bypass test-env gate via direct internal call to
# ingest_webhook from within a mocked poll. Easiest path is to drive
# poll_recent_issues with APP_ENV=production + mocked HTTP + mocked
# Redis cooldown.
# ---------------------------------------------------------------------------

class _NoCooldownRedis:
    """Stand-in Redis client that always reports no cooldown active."""
    def exists(self, _key):
        return False
    def set(self, *_args, **_kwargs):
        return True


def _force_prod_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SENTRY_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("SENTRY_ORG", "hedgespark")
    monkeypatch.setenv("SENTRY_PROJECT", "python-fastapi")


def test_high_volume_recent_issue_is_forwarded(db, monkeypatch):
    """An issue with count >=10 firing in the last 60min is fed to ingest_webhook."""
    _force_prod_env(monkeypatch)

    from datetime import datetime, timezone
    just_now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    fake_issue = {
        "id": "999001",
        "title": "NameError: name 'row' is not defined",
        "culprit": "app.api.setup.get_pixel_status",
        "count": "640",
        "lastSeen": just_now,
        "platform": "python",
        "metadata": {"type": "NameError", "value": "name 'row' is not defined"},
    }

    forwarded = []

    def _fake_ingest(_db, *, payload, sentry_event_id=None):
        forwarded.append(sentry_event_id)
        return {"status": "new", "incident_id": 12345}

    monkeypatch.setattr(sentry_poller, "_fetch_active_issues", lambda *a, **k: [fake_issue])
    monkeypatch.setattr(sentry_poller, "_fetch_latest_event", lambda *a, **k: {})
    monkeypatch.setattr(sentry_poller, "_redis_cooldown_active", lambda: False)
    monkeypatch.setattr(sentry_poller, "_redis_cooldown_arm", lambda: None)
    # Patch ingest_webhook at the import site inside poll_recent_issues.
    monkeypatch.setattr(
        "app.services.sentry_triage.ingest_webhook",
        _fake_ingest,
    )

    result = sentry_poller.poll_recent_issues(db)
    assert result["status"] == "ok"
    assert result["forwarded"] == 1
    assert len(forwarded) == 1
    assert forwarded[0].startswith("poll:999001:")


def test_stale_issue_is_skipped(db, monkeypatch):
    """Issue lastSeen >60min ago is skipped without forwarding."""
    _force_prod_env(monkeypatch)

    fake_issue = {
        "id": "999002",
        "title": "ProgrammingError",
        "count": "100",
        "lastSeen": "2026-04-20T00:00:00Z",  # 5 days old
    }

    monkeypatch.setattr(sentry_poller, "_fetch_active_issues", lambda *a, **k: [fake_issue])
    monkeypatch.setattr(sentry_poller, "_fetch_latest_event", lambda *a, **k: {})
    monkeypatch.setattr(sentry_poller, "_redis_cooldown_active", lambda: False)
    monkeypatch.setattr(sentry_poller, "_redis_cooldown_arm", lambda: None)

    result = sentry_poller.poll_recent_issues(db)
    assert result["status"] == "ok"
    assert result["forwarded"] == 0
    assert result["skipped_stale"] == 1


def test_low_volume_issue_is_skipped(db, monkeypatch):
    """Issue with count <10 is skipped — only flooding-class events are
    worth feeding to the LLM-driven pipeline."""
    _force_prod_env(monkeypatch)

    from datetime import datetime, timezone
    just_now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    fake_issue = {
        "id": "999003",
        "title": "Trivial one-off",
        "count": "3",
        "lastSeen": just_now,
    }

    monkeypatch.setattr(sentry_poller, "_fetch_active_issues", lambda *a, **k: [fake_issue])
    monkeypatch.setattr(sentry_poller, "_fetch_latest_event", lambda *a, **k: {})
    monkeypatch.setattr(sentry_poller, "_redis_cooldown_active", lambda: False)
    monkeypatch.setattr(sentry_poller, "_redis_cooldown_arm", lambda: None)

    result = sentry_poller.poll_recent_issues(db)
    assert result["status"] == "ok"
    assert result["forwarded"] == 0
    assert result["skipped_low_volume"] == 1


def test_cooldown_short_circuits(db, monkeypatch):
    """When cooldown is active, return cleanly without hitting the API."""
    _force_prod_env(monkeypatch)

    api_calls = []
    monkeypatch.setattr(
        sentry_poller,
        "_fetch_active_issues",
        lambda *a, **k: api_calls.append("called") or [],
    )
    monkeypatch.setattr(sentry_poller, "_redis_cooldown_active", lambda: True)

    result = sentry_poller.poll_recent_issues(db)
    assert result == {"status": "skipped", "reason": "cooldown"}
    assert api_calls == []  # NO API call when cooldown is active


def test_dict_shaped_release_field_does_not_crash_parser(monkeypatch):
    """REGRESSION: the Sentry REST `events/latest` response carries
    `event.release` as a dict ({id, version, ...}), not a string. The
    parser used to assign the dict directly to `result['release']`,
    then sentry_triage.`incident.release = release[:128]` crashed with
    `TypeError: unhashable type: 'slice'`. Fix in parse_sentry_webhook
    extracts release.version. Live-observed 2026-04-25 evening: 8
    SentryIncident rows successfully stored then rolled back due to
    this exception."""
    from app.services.sentry_parser import parse_sentry_webhook

    payload = {
        "data": {
            "issue": {
                "id": "999",
                "title": "test",
                "metadata": {"type": "Err", "value": "x"},
                "tags": [],
            },
            "event": {
                "release": {"id": 77460591, "version": "hedgespark@abc123"},
                "tags": [],
            },
        }
    }
    parsed = parse_sentry_webhook(payload)
    assert parsed.get("release") == "hedgespark@abc123"

    # Verify slice safety end-to-end: the field as returned must
    # support :128 without raising.
    sliced = (parsed.get("release") or "")[:128]
    assert isinstance(sliced, str)


def test_string_release_field_passes_through(monkeypatch):
    """The webhook (vs REST) shape carries release as a string. Confirm
    the normalizer doesn't break that path."""
    from app.services.sentry_parser import parse_sentry_webhook

    payload = {
        "data": {
            "issue": {"id": "1", "title": "x", "metadata": {}, "tags": []},
            "event": {"release": "hedgespark@xyz", "tags": []},
        }
    }
    parsed = parse_sentry_webhook(payload)
    assert parsed.get("release") == "hedgespark@xyz"
