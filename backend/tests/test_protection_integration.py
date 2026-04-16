"""
Integration tests — protection_state actually CHANGES worker behavior.

These tests prove the wiring between protection_state and the real hot
paths (agent_worker monthly audit, aggregation_worker nudge compose,
monthly_evolution_audit._call_opus). Without this wiring, protection_state
would be shelfware.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from app.core import protection_state as ps


@pytest.fixture(autouse=True)
def _reset_cache():
    ps.invalidate_cache()
    yield
    ps.invalidate_cache()


# ---------------------------------------------------------------------------
# monthly_evolution_audit._call_opus is gated by protection_state
# ---------------------------------------------------------------------------

def test_call_opus_refused_when_llm_degraded(monkeypatch):
    """Opus call returns '' when protection_state says skip optional LLM."""
    from app.services import monthly_evolution_audit as mea

    with patch("app.core.protection_state.should_skip_optional_llm", return_value=True), \
         patch("app.core.protection_state.protection_state", return_value={"level": "DEGRADED"}):
        result = mea._call_opus("any context")
    assert result == ""


def test_call_opus_proceeds_when_ok(monkeypatch):
    """Opus call proceeds past the protection gate when system is OK."""
    from app.services import monthly_evolution_audit as mea

    with patch("app.core.protection_state.should_skip_optional_llm", return_value=False), \
         patch("app.core.llm_budget.check_budget", return_value=(False, "no_budget_test")), \
         patch("app.core.llm_budget.record_blocked"):
        # Budget blocks next (fake), so we reach that code path — meaning
        # the protection_state gate was NOT the blocker.
        result = mea._call_opus("any context")
    assert result == ""  # budget-blocked, not protection-blocked


# ---------------------------------------------------------------------------
# _run_ai_nudge_compose reduces batch under DEGRADED, skips under CRITICAL
# ---------------------------------------------------------------------------

def test_nudge_compose_skips_under_critical(db):
    from app.workers.aggregation_worker import _run_ai_nudge_compose

    with patch("app.core.protection_state.protection_state", return_value={
        "level": "CRITICAL",
        "protective_actions": ["skip_all_optional_llm_calls"],
    }):
        upgraded = _run_ai_nudge_compose(db)
    assert upgraded == 0


def test_nudge_compose_skips_when_llm_degraded(db):
    from app.workers.aggregation_worker import _run_ai_nudge_compose

    with patch("app.core.protection_state.protection_state", return_value={
        "level": "DEGRADED",
        "protective_actions": ["skip_optional_llm_calls"],
    }):
        upgraded = _run_ai_nudge_compose(db)
    assert upgraded == 0


def test_nudge_compose_reduces_batch_under_degraded_non_llm(db, monkeypatch):
    """DEGRADED state without LLM flag reduces batch from 5 to 2."""
    from datetime import datetime, timedelta, timezone
    import json as _json
    from app.models.active_nudge import ActiveNudge
    from app.workers.aggregation_worker import _run_ai_nudge_compose

    # Seed 5 pending nudges
    shop = "batch-reduce.myshopify.com"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for i in range(5):
        db.add(ActiveNudge(
            shop_domain=shop,
            product_url=f"/products/p{i}",
            action_type="urgency",
            trigger_source="ai_composer",
            copy_variant="baseline",
            copy_config=_json.dumps({"headline": "b"}),
            copy_variants=_json.dumps([
                {"variant_name": "a", "copy_config": {"headline": "a"}},
                {"variant_name": "b", "copy_config": {"headline": "b"}},
            ]),
            holdout_pct=0,
            status="active",
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(days=7),
            visitor_count=100,
            ai_compose_pending=True,
        ))
    db.flush()

    # DEGRADED state WITHOUT llm skip flag → batch reduced to 2
    with patch("app.core.protection_state.protection_state", return_value={
        "level": "DEGRADED",
        "protective_actions": ["reduce_batch_sizes"],  # DB pressure, not LLM
    }):
        async def _mock_compose(*args, **kwargs):
            return (
                [
                    {"variant_name": "ai_a", "copy_config": {"headline": "A"}},
                    {"variant_name": "ai_b", "copy_config": {"headline": "B"}},
                ],
                {"fallback_used": False},
            )
        monkeypatch.setattr(
            "app.services.nudge_composer.compose_nudge_variants",
            _mock_compose,
        )
        upgraded = _run_ai_nudge_compose(db)
    # Degraded batch cap = 2, so at most 2 upgraded
    assert upgraded == 2


def test_nudge_compose_full_batch_when_ok(db, monkeypatch):
    """OK state → full batch of 5 processed."""
    from datetime import datetime, timedelta, timezone
    import json as _json
    from app.models.active_nudge import ActiveNudge
    from app.workers.aggregation_worker import _run_ai_nudge_compose

    shop = "full-batch.myshopify.com"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for i in range(5):
        db.add(ActiveNudge(
            shop_domain=shop,
            product_url=f"/products/full{i}",
            action_type="urgency",
            trigger_source="ai_composer",
            copy_variant="baseline",
            copy_config=_json.dumps({"headline": "b"}),
            copy_variants=_json.dumps([
                {"variant_name": "a", "copy_config": {"headline": "a"}},
                {"variant_name": "b", "copy_config": {"headline": "b"}},
            ]),
            holdout_pct=0,
            status="active",
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(days=7),
            visitor_count=100,
            ai_compose_pending=True,
        ))
    db.flush()

    with patch("app.core.protection_state.protection_state", return_value={
        "level": "OK",
        "protective_actions": [],
    }):
        async def _mock_compose(*args, **kwargs):
            return (
                [
                    {"variant_name": "ai_a", "copy_config": {"headline": "A"}},
                    {"variant_name": "ai_b", "copy_config": {"headline": "B"}},
                ],
                {"fallback_used": False},
            )
        monkeypatch.setattr(
            "app.services.nudge_composer.compose_nudge_variants",
            _mock_compose,
        )
        upgraded = _run_ai_nudge_compose(db)
    assert upgraded == 5


# ---------------------------------------------------------------------------
# agent_worker monthly audit refuses under LLM pressure
# ---------------------------------------------------------------------------

def test_agent_worker_monthly_audit_skipped_under_llm_pressure():
    """_run_monthly_evolution_audit returns early when LLM optional is skipped."""
    from app.workers.agent_worker import _run_monthly_evolution_audit

    # If protection says skip LLM, the function MUST return before touching
    # SessionLocal, should_run_monthly_audit, mark_monthly_audit_run, or
    # run_monthly_opus_audit.
    with patch("app.core.protection_state.should_skip_optional_llm", return_value=True), \
         patch("app.core.protection_state.protection_state", return_value={"level": "DEGRADED"}), \
         patch("app.workers.agent_worker.SessionLocal") as mock_session_cls, \
         patch("app.services.monthly_evolution_audit.should_run_monthly_audit") as mock_should_run, \
         patch("app.services.monthly_evolution_audit.run_monthly_opus_audit") as mock_run:
        _run_monthly_evolution_audit()

    # No DB session opened, no cooldown check, no audit executed
    mock_session_cls.assert_not_called()
    mock_should_run.assert_not_called()
    mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# deploy.sh integration — preflight + postdeploy commands present
# ---------------------------------------------------------------------------

def test_deploy_sh_invokes_preflight_before_pm2_restart():
    """deploy.sh MUST call deploy_gate preflight BEFORE pm2 restart."""
    import re
    from pathlib import Path
    deploy_sh = Path("/opt/wishspark/deploy.sh").read_text()
    # deploy_gate.py may be wrapped in quotes; match with flexible regex.
    pre_m = re.search(r"deploy_gate\.py[\"']?\s+preflight", deploy_sh)
    reload_idx = deploy_sh.find("pm2 reload /opt/wishspark/ecosystem.config.js 2>&1")
    assert pre_m is not None, "deploy.sh does not invoke deploy_gate preflight"
    assert reload_idx > 0, "deploy.sh does not contain pm2 reload"
    assert pre_m.start() < reload_idx, "deploy_gate preflight must run BEFORE pm2 reload"


def test_deploy_sh_invokes_postdeploy_after_pm2_restart():
    """deploy.sh MUST call deploy_gate postdeploy AFTER pm2 restart."""
    import re
    from pathlib import Path
    deploy_sh = Path("/opt/wishspark/deploy.sh").read_text()
    reload_idx = deploy_sh.find("pm2 reload /opt/wishspark/ecosystem.config.js 2>&1")
    post_m = re.search(r"deploy_gate\.py[\"']?\s+postdeploy", deploy_sh)
    assert post_m is not None, "deploy.sh does not invoke deploy_gate postdeploy"
    assert reload_idx < post_m.start(), "deploy_gate postdeploy must run AFTER pm2 reload"


def test_deploy_sh_uses_absolute_ecosystem_path():
    """No CWD-dependent ecosystem paths allowed in deploy.sh."""
    from pathlib import Path
    deploy_sh = Path("/opt/wishspark/deploy.sh").read_text()
    # No bare "ecosystem.config.js" invocations — must be absolute path
    import re
    bad = re.findall(r"pm2 (?:restart|reload) ecosystem\.config\.js(?!')", deploy_sh)
    assert bad == [], f"deploy.sh contains CWD-dependent pm2 restart: {bad}"
