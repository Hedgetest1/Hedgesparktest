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
# deploy.sh integration — preflight + postdeploy commands present
# ---------------------------------------------------------------------------

def test_deploy_sh_invokes_preflight_before_pm2_restart():
    """deploy.sh MUST call deploy_gate preflight BEFORE the main deploy pm2 restart.
    Note: the do_rollback() function also contains a pm2 reload — we only check
    the MAIN deploy path by stripping the rollback function body first."""
    import re
    import os
    from pathlib import Path
    _repo_root = Path(os.environ.get("REPO_ROOT", Path(__file__).parent.parent.parent))
    deploy_sh_path = _repo_root / "deploy.sh"
    if not deploy_sh_path.exists():
        pytest.skip("deploy.sh not present in this checkout")
    deploy_sh = deploy_sh_path.read_text()
    # Strip do_rollback() function body so its internal pm2 reload doesn't interfere.
    # do_rollback() is a top-level shell function; strip from "do_rollback() {" to the
    # closing "}" at its indentation level.
    main_body = re.sub(r'\ndo_rollback\(\)\s*\{.*?\n\}', '', deploy_sh, flags=re.DOTALL)
    # deploy_gate.py may be wrapped in quotes; match with flexible regex.
    pre_m = re.search(r"deploy_gate\.py[\"']?\s+preflight", main_body)
    reload_m = re.search(r"pm2 reload .+ecosystem\.config\.js", main_body)
    assert pre_m is not None, "deploy.sh does not invoke deploy_gate preflight"
    assert reload_m is not None, "deploy.sh main path does not contain pm2 reload"
    assert pre_m.start() < reload_m.start(), "deploy_gate preflight must run BEFORE pm2 reload"


def test_deploy_sh_invokes_postdeploy_after_pm2_restart():
    """deploy.sh MUST call deploy_gate postdeploy AFTER the main deploy pm2 restart."""
    import re
    import os
    from pathlib import Path
    _repo_root = Path(os.environ.get("REPO_ROOT", Path(__file__).parent.parent.parent))
    deploy_sh_path = _repo_root / "deploy.sh"
    if not deploy_sh_path.exists():
        pytest.skip("deploy.sh not present in this checkout")
    deploy_sh = deploy_sh_path.read_text()
    # Strip do_rollback() so its pm2 reload doesn't interfere with ordering check.
    main_body = re.sub(r'\ndo_rollback\(\)\s*\{.*?\n\}', '', deploy_sh, flags=re.DOTALL)
    reload_m = re.search(r"pm2 reload .+ecosystem\.config\.js", main_body)
    post_m = re.search(r"deploy_gate\.py[\"']?\s+postdeploy", main_body)
    assert post_m is not None, "deploy.sh does not invoke deploy_gate postdeploy"
    assert reload_m is not None, "deploy.sh main path does not contain pm2 reload"
    assert reload_m.start() < post_m.start(), "deploy_gate postdeploy must run AFTER pm2 reload"


def test_deploy_sh_uses_absolute_ecosystem_path():
    """No CWD-dependent ecosystem paths allowed in deploy.sh."""
    import re
    import os
    from pathlib import Path
    _repo_root = Path(os.environ.get("REPO_ROOT", Path(__file__).parent.parent.parent))
    deploy_sh_path = _repo_root / "deploy.sh"
    if not deploy_sh_path.exists():
        pytest.skip("deploy.sh not present in this checkout")
    deploy_sh = deploy_sh_path.read_text()
    # No bare "ecosystem.config.js" invocations — must be absolute path
    bad = re.findall(r"pm2 (?:restart|reload) ecosystem\.config\.js(?!')", deploy_sh)
    assert bad == [], f"deploy.sh contains CWD-dependent pm2 restart: {bad}"
