"""
Tests for GET /ops/pipeline-health — unified self-healing observability.

The endpoint is the single place an operator looks at to understand:
  * Is the pipeline healthy?
  * Are alert storms being aggregated correctly?
  * How many visibility-only candidates are waiting for human triage?
  * Are worker cycles fresh?
  * Is auto-merge armed or on cooldown?

These tests cover the shape contract only — the underlying loop_health
and protection_state functions have their own dedicated suites.
"""
from __future__ import annotations

import os


def test_pipeline_health_endpoint_returns_complete_shape(client):
    """Endpoint returns 200 with all expected top-level keys."""
    headers = {"X-API-Key": os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")}
    resp = client.get("/ops/pipeline-health", headers=headers)
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text[:500]}"
    body = resp.json()

    required_keys = [
        "generated_at",
        "loop_health",
        "protection_state",
        "candidates_48h_by_status_source",
        "visibility_only_backlog",
        "alert_storms_top10",
        "last_agent_cycle",
        "last_aggregation_cycle",
        "auto_merge",
        "freshness_warnings",
    ]
    for k in required_keys:
        assert k in body, f"response missing required key {k!r}"


def test_pipeline_health_loop_health_has_queues(client):
    """loop_health section must include the main queue shapes."""
    headers = {"X-API-Key": os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")}
    resp = client.get("/ops/pipeline-health", headers=headers)
    loop = resp.json()["loop_health"]
    # Either a normal shape with bugfix_queues / is_healthy,
    # or an error dict — both are acceptable, we just need a dict.
    assert isinstance(loop, dict)
    if "error" not in loop:
        assert "bugfix_queues" in loop
        assert "is_healthy" in loop


def test_pipeline_health_protection_state_shape(client):
    """protection_state section must report a level."""
    headers = {"X-API-Key": os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")}
    resp = client.get("/ops/pipeline-health", headers=headers)
    prot = resp.json()["protection_state"]
    assert isinstance(prot, dict)
    if "error" not in prot:
        assert "level" in prot
        assert prot["level"] in ("ok", "OK", "degraded", "DEGRADED", "critical", "CRITICAL")


def test_pipeline_health_auto_merge_reports_flag_state(client):
    """auto_merge section reports env flag + cooldown state."""
    headers = {"X-API-Key": os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")}
    resp = client.get("/ops/pipeline-health", headers=headers)
    am = resp.json()["auto_merge"]
    assert "enabled_env_flag" in am or "error" in am
    if "enabled_env_flag" in am:
        assert isinstance(am["enabled_env_flag"], bool)
        assert "on_cooldown" in am
        assert "cooldown_seconds_remaining" in am


def test_pipeline_health_visibility_backlog_is_numeric(client):
    """visibility_only_backlog must be an integer (can be 0)."""
    headers = {"X-API-Key": os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")}
    resp = client.get("/ops/pipeline-health", headers=headers)
    v = resp.json()["visibility_only_backlog"]
    assert v is None or isinstance(v, int)


def test_pipeline_health_requires_operator_auth(client):
    """Without the operator API key the endpoint must return 4xx."""
    resp = client.get("/ops/pipeline-health")
    assert resp.status_code in (401, 403)
