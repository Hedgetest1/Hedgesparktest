"""Tests for monthly Opus evolution audit, system summary, and Telegram agent."""
import json
import time
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

import app.services.monthly_evolution_audit as audit_mod
from app.services.monthly_evolution_audit import (
    should_run_monthly_audit,
    mark_monthly_audit_run,
    run_monthly_opus_audit,
    _parse_proposals,
    _store_proposals,
    _audit_cycle_id,
    MAX_PROPOSALS_PER_RUN,
)
from app.services.system_summary import (
    build_system_summary,
    _get_ram_usage,
    _get_cpu_load,
    _generate_warnings,
)
from app.services.telegram_agent import (
    handle_command,
    send_message,
    send_monthly_report,
    is_configured,
)
from app.models.evolution_proposal import EvolutionProposal


# ---------------------------------------------------------------------------
# Monthly cooldown
# ---------------------------------------------------------------------------

def test_cooldown_respected():
    """Audit must respect 30-day cooldown."""
    original = audit_mod._last_audit_run
    try:
        audit_mod._last_audit_run = None
        # Clear Redis cooldown key to ensure clean state
        with patch("app.core.redis_client.cache_get", return_value=None):
            assert should_run_monthly_audit() is True

        mark_monthly_audit_run()
        assert should_run_monthly_audit() is False
    finally:
        audit_mod._last_audit_run = original


def test_cooldown_expired():
    """Audit should run when cooldown has expired (in-process + Redis)."""
    original = audit_mod._last_audit_run
    try:
        # Set last run to 31 days ago
        audit_mod._last_audit_run = time.monotonic() - (31 * 86400)
        # Redis cooldown key must also be absent for audit to run
        with patch("app.core.redis_client.cache_get", return_value=None):
            assert should_run_monthly_audit() is True
    finally:
        audit_mod._last_audit_run = original


# ---------------------------------------------------------------------------
# Proposal parsing + safety enforcement
# ---------------------------------------------------------------------------

def test_max_proposals_cap_enforced():
    """Parser must cap at MAX_PROPOSALS_PER_RUN."""
    proposals = [
        _valid_bet(title=f"Improve conversion nudge variant {i} for high-intent visitors")
        for i in range(20)
    ]
    raw = json.dumps({"bets": proposals})
    result = _parse_proposals(raw)
    assert len(result) <= MAX_PROPOSALS_PER_RUN


def _valid_bet(**overrides):
    """Build a proposal that passes all strict governance gates."""
    base = {
        "title": "Improve conversion nudge targeting for high-intent visitors",
        "type": "performance",
        "revenue_thesis": "Targeting high-intent visitors with nudges will increase CVR by 12% based on current 2.1% baseline, adding ~€300/month per active merchant",
        "expected_impact": "Increase conversion rate by 12% across top 20 products based on current 2.1% baseline",
        "risk_level": "LEVEL_2",
        "rejected_alternatives": [
            {"alternative": "Broader audience targeting", "why_rejected": "Lower precision leads to nudge fatigue and reduced trust"},
            {"alternative": "Static discount banners", "why_rejected": "Margin erosion without behavioral evidence of purchase intent"},
        ],
    }
    base.update(overrides)
    return base


def test_risk_level_enforced():
    """LEVEL_1 proposals are upgraded to LEVEL_3."""
    raw = json.dumps({"bets": [_valid_bet(risk_level="LEVEL_1")]})
    result = _parse_proposals(raw)
    assert len(result) == 1
    assert result[0]["risk_level"] == "LEVEL_3"


def test_invalid_type_rejected():
    """Invalid type is rejected (strict governance — no fallback to architecture)."""
    raw = json.dumps({"bets": [_valid_bet(type="banana")]})
    result = _parse_proposals(raw)
    assert result == []


def test_parse_invalid_json():
    """Invalid JSON returns empty list."""
    assert _parse_proposals("not json") == []
    assert _parse_proposals("") == []
    assert _parse_proposals(None) == []


def test_parse_empty_proposals():
    """Valid JSON with no proposals returns empty."""
    assert _parse_proposals(json.dumps({"proposals": []})) == []


# ---------------------------------------------------------------------------
# No auto-apply from monthly audit
# ---------------------------------------------------------------------------

def test_no_auto_apply(db):
    """Monthly audit proposals must never be auto_applicable."""
    cycle = _audit_cycle_id()
    proposals = [
        {"title": "Test proposal", "type": "architecture",
         "reasoning": "test", "expected_impact": "test", "risk_level": "LEVEL_2"},
    ]
    stored = _store_proposals(db, proposals, cycle)
    assert stored == 1

    row = db.query(EvolutionProposal).filter(
        EvolutionProposal.audit_cycle == cycle,
    ).first()
    assert row is not None
    assert row.auto_applicable is False
    assert row.risk_level in ("LEVEL_2", "LEVEL_3")


def test_dedup_prevents_duplicates(db):
    """Same proposal in same cycle is deduped."""
    cycle = _audit_cycle_id()
    proposals = [
        {"title": "Duplicate test", "type": "reliability",
         "reasoning": "r", "expected_impact": "e", "risk_level": "LEVEL_2"},
    ]
    first = _store_proposals(db, proposals, cycle)
    second = _store_proposals(db, proposals, cycle)
    assert first == 1
    assert second == 0


# ---------------------------------------------------------------------------
# Full audit with mocked LLM
# ---------------------------------------------------------------------------

def test_audit_skipped_without_api_key(db):
    """Audit returns skipped when no API key."""
    original = audit_mod._last_audit_run
    try:
        audit_mod._last_audit_run = None  # ensure not blocked by cooldown
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
            result = run_monthly_opus_audit(db)
            assert result["status"] == "skipped"
            assert result["proposals_created"] == 0
    finally:
        audit_mod._last_audit_run = original


def test_audit_stores_proposals_on_success(db):
    """Successful LLM call stores proposals."""
    mock_response = json.dumps({
        "bets": [
            _valid_bet(title="Improve conversion nudge latency for high-intent cart visitors", type="performance"),
            _valid_bet(title="Add holdout measurement to attribution funnel for causal lift", type="reliability"),
        ],
        "summary": "System is healthy but conversion path needs attention",
    })

    with patch("app.services.monthly_evolution_audit._call_opus", return_value=mock_response):
        result = run_monthly_opus_audit(db)
        db.flush()

    assert result["status"] == "completed"
    assert result["proposals_created"] == 2
    assert len(result["proposals"]) == 2


# ---------------------------------------------------------------------------
# Audit log entry
# ---------------------------------------------------------------------------

def test_audit_creates_audit_log(db):
    """Successful audit writes audit_log entry."""
    mock_response = json.dumps({
        "bets": [_valid_bet(title="Improve conversion nudge reliability for signal tracking")],
    })

    with patch("app.services.monthly_evolution_audit._call_opus", return_value=mock_response):
        run_monthly_opus_audit(db)
        db.flush()

    from app.models.audit_log import AuditLog
    log = db.query(AuditLog).filter(
        AuditLog.action_type == "monthly_evolution_audit",
    ).first()
    assert log is not None
    assert log.actor_name == "monthly_opus_audit"


# ---------------------------------------------------------------------------
# System summary
# ---------------------------------------------------------------------------

def test_system_summary_structure(db):
    """build_system_summary returns expected structure."""
    s = build_system_summary(db)

    assert "timestamp" in s
    assert "infra" in s
    assert "llm_usage" in s
    assert "cost_estimate" in s
    assert "warnings" in s
    assert isinstance(s["warnings"], list)

    # Infra sub-keys
    assert "ram" in s["infra"]
    assert "cpu" in s["infra"]
    assert "workers" in s["infra"]

    # Cost sub-keys
    assert "fixed_monthly_eur" in s["cost_estimate"]
    assert "total_monthly_eur" in s["cost_estimate"]


def test_ram_usage_returns_dict():
    """RAM metrics return a dict even if unavailable."""
    result = _get_ram_usage()
    assert isinstance(result, dict)
    assert "total_mb" in result
    assert "usage_pct" in result


def test_cpu_load_returns_dict():
    """CPU metrics return a dict."""
    result = _get_cpu_load()
    assert isinstance(result, dict)
    assert "load_5m" in result


def test_warnings_ram_high():
    """Warning generated when RAM > 85%."""
    warnings = _generate_warnings(
        ram={"usage_pct": 90},
        cpu={"normalized_pct": 30},
        workers={"error_rate_pct": 2},
        llm={"global_calls_today": 10, "global_max_per_day": 150, "blocked_today": 0},
    )
    assert any("RAM" in w for w in warnings)


def test_warnings_llm_near_cap():
    """Warning generated when LLM calls > 80% of cap."""
    warnings = _generate_warnings(
        ram={"usage_pct": 50},
        cpu={"normalized_pct": 30},
        workers={"error_rate_pct": 2},
        llm={"global_calls_today": 130, "global_max_per_day": 150, "blocked_today": 0},
    )
    assert any("LLM" in w or "cap" in w for w in warnings)


def test_no_warnings_when_healthy():
    """No warnings when all metrics are normal."""
    warnings = _generate_warnings(
        ram={"usage_pct": 40},
        cpu={"normalized_pct": 20},
        workers={"error_rate_pct": 1},
        llm={"global_calls_today": 5, "global_max_per_day": 150, "blocked_today": 0},
    )
    assert len(warnings) == 0


# ---------------------------------------------------------------------------
# Telegram agent
# ---------------------------------------------------------------------------

def test_telegram_not_configured():
    """is_configured returns False when env vars missing."""
    with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}, clear=False):
        # Reload module-level vars
        import app.services.telegram_agent as tg
        orig_token, orig_chat = tg._BOT_TOKEN, tg._CHAT_ID
        tg._BOT_TOKEN = ""
        tg._CHAT_ID = ""
        try:
            assert tg.is_configured() is False
        finally:
            tg._BOT_TOKEN, tg._CHAT_ID = orig_token, orig_chat


def test_send_message_no_token():
    """send_message returns False without token."""
    import app.services.telegram_agent as tg
    orig = tg._BOT_TOKEN
    tg._BOT_TOKEN = ""
    try:
        assert send_message("test") is False
    finally:
        tg._BOT_TOKEN = orig


def test_send_message_success():
    """send_message returns truthy (message_id or True) on 200."""
    import app.services.telegram_agent as tg
    orig_token, orig_chat = tg._BOT_TOKEN, tg._CHAT_ID
    tg._BOT_TOKEN = "fake-token"
    tg._CHAT_ID = "123"
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"result": {"message_id": 42}}
    mock_client = MagicMock()
    mock_client.post.return_value = mock_resp
    mock_client.is_closed = False
    try:
        with patch("app.services.telegram_agent._get_http_client", return_value=mock_client), \
             patch("app.core.notifier_guard.is_real_send_allowed", return_value=True):
            result = send_message("Hello")
            assert result  # truthy (42 or True)
            mock_client.post.assert_called_once()
    finally:
        tg._BOT_TOKEN, tg._CHAT_ID = orig_token, orig_chat


def test_handle_help_command():
    """Help command returns usage text."""
    result = handle_command("/help")
    assert "HedgeSpark" in result
    assert "/status" in result
    assert "/costs" in result


def test_handle_status_command(db):
    """Status command returns system info (CTO health model)."""
    result = handle_command("/status", db=db)
    assert "Status" in result or "RAM" in result or "HEALTHY" in result or "DEGRADED" in result


def test_handle_costs_command(db):
    """Costs command returns cost breakdown."""
    result = handle_command("/costs", db=db)
    assert "€" in result or "Cost" in result


def test_handle_unknown_command():
    """Unknown command returns help suggestion."""
    result = handle_command("/banana")
    assert "Unknown" in result or "/help" in result


def test_handle_merchants_command(db):
    """Merchants command returns something useful."""
    result = handle_command("/merchants", db=db)
    assert "erchant" in result  # "Merchant" or "merchant"


@patch("app.services.telegram_agent.send_message", return_value=True)
def test_monthly_report_format(mock_send):
    """Monthly report message is well-formatted."""
    proposals = [
        {"title": "Improve caching", "type": "performance"},
        {"title": "Split ops.py", "type": "architecture"},
    ]
    summary = {
        "infra": {
            "ram": {"usage_pct": 65, "used_mb": 1300, "total_mb": 2000},
            "workers": {"error_rate_pct": 2, "cycles_24h": 96},
        },
        "llm_usage": {"global_calls_today": 42},
        "cost_estimate": {
            "fixed_monthly_eur": {"server_vps": 25.0},
            "llm_monthly_eur": 3.50,
            "total_monthly_eur": 30.50,
        },
        "warnings": ["Consider upgrading server tier"],
    }

    result = send_monthly_report(proposals, summary)
    assert result is True

    sent_text = mock_send.call_args[0][0]
    assert "Monthly Evolution Report" in sent_text
    assert "Improve caching" in sent_text
    assert "€" in sent_text
    assert "/evolution" in sent_text
