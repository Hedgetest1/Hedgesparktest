"""Tests for security_preflight_guard — the hard wall against
self-modifying-pipeline regressions in security and GDPR.

Contract: for every rule in the guard, proposing a diff that matches
it must result in `guard_candidate(candidate)` returning
`(False, reason)` — no negotiation, no TIER_2 escalation, no grace.

Sensitive-path hits are a separate class: they escalate to TIER_2
rather than reject outright.
"""
from __future__ import annotations

from app.models.bugfix_candidate import BugFixCandidate
from app.services.security_preflight_guard import (
    guard_candidate,
    scan_diff_for_security_regressions,
)


def _diff(target: str, body: str) -> str:
    lines = body.split("\n")
    header = [f"--- a/{target}", f"+++ b/{target}",
              f"@@ -1,1 +1,{len(lines)+1} @@", " pass"]
    for ln in lines:
        header.append("+" + ln)
    return "\n".join(header) + "\n"


def _remove_diff(target: str, removed: str, added: str = "") -> str:
    lines = [f"--- a/{target}", f"+++ b/{target}", "@@ -1,5 +1,5 @@"]
    for ln in removed.split("\n"):
        lines.append("-" + ln)
    for ln in added.split("\n"):
        if ln:
            lines.append("+" + ln)
    return "\n".join(lines) + "\n"


def _cand(diff: str) -> BugFixCandidate:
    c = BugFixCandidate()
    c.patch_diff = diff
    return c


# ---------- Added-line rules ----------

def test_blocks_pii_email_in_log():
    d = _diff("app/services/foo.py",
              'log.info("delivered to %s", to_email)')
    violations = scan_diff_for_security_regressions(d)
    assert any(v["code"] == "GDPR-01" for v in violations)
    allowed, reason = guard_candidate(_cand(d))
    assert allowed is False
    assert "GDPR-01" in reason


def test_accepts_masked_email_in_log():
    d = _diff("app/services/foo.py",
              'log.info("delivered to %s", mask_email(to_email))')
    violations = scan_diff_for_security_regressions(d)
    assert not any(v["code"] == "GDPR-01" for v in violations)


def test_blocks_shell_true_subprocess():
    d = _diff("app/services/foo.py",
              'subprocess.run("ls -la", shell=True)')
    allowed, reason = guard_candidate(_cand(d))
    assert allowed is False
    assert "SEC-01" in reason


def test_blocks_os_system():
    d = _diff("app/services/foo.py", 'os.system("rm -rf /tmp/x")')
    allowed, reason = guard_candidate(_cand(d))
    assert allowed is False
    assert "SEC-01" in reason


def test_blocks_pickle_loads():
    d = _diff("app/services/foo.py", "data = pickle.loads(raw)")
    allowed, reason = guard_candidate(_cand(d))
    assert allowed is False
    assert "SEC-02" in reason


def test_blocks_eval():
    d = _diff("app/services/foo.py", 'result = eval(user_input)')
    allowed, reason = guard_candidate(_cand(d))
    assert allowed is False
    assert "SEC-02" in reason


def test_blocks_raw_sql_fstring_interpolation():
    d = _diff("app/services/foo.py",
              'db.execute(text(f"SELECT * FROM t WHERE id = {uid}"))')
    allowed, reason = guard_candidate(_cand(d))
    assert allowed is False
    assert "SEC-03" in reason


def test_blocks_hardcoded_secret_shape():
    d = _diff("app/services/foo.py",
              "TOKEN = 'sk-1234567890abcdef1234567890'")
    allowed, reason = guard_candidate(_cand(d))
    assert allowed is False
    assert "SEC-04" in reason


def test_blocks_consent_bypass_lambda():
    d = _diff("app/api/track.py",
              "_consent_allows_ingestion = lambda p: True")
    allowed, reason = guard_candidate(_cand(d))
    assert allowed is False
    assert "GDPR-02" in reason


# ---------- Removed-line rules ----------

def test_blocks_removal_of_compare_digest():
    d = _remove_diff(
        "app/core/deps.py",
        "if hmac.compare_digest(x_api_key, _OPERATOR_KEY):",
        "if x_api_key == _OPERATOR_KEY:",
    )
    violations = scan_diff_for_security_regressions(d)
    assert any(v["code"] == "SEC-08" for v in violations)


def test_blocks_removal_of_verify_webhook():
    d = _remove_diff(
        "app/api/resend_webhooks.py",
        "_verify_webhook(payload_str, headers)",
    )
    violations = scan_diff_for_security_regressions(d)
    assert any(v["code"] == "SEC-09" for v in violations)


def test_blocks_removal_of_consume_nonce():
    d = _remove_diff(
        "app/api/shopify_oauth.py",
        "if not state or not _consume_nonce(shop, state):",
    )
    violations = scan_diff_for_security_regressions(d)
    assert any(v["code"] == "SEC-10" for v in violations)


def test_blocks_removal_of_consent_gate():
    d = _remove_diff(
        "app/api/track.py",
        "if not _consent_allows_ingestion(payload):",
    )
    violations = scan_diff_for_security_regressions(d)
    assert any(v["code"] == "GDPR-03" for v in violations)


def test_blocks_removal_of_learning_isolation_gate():
    d = _remove_diff(
        "app/services/bugfix_pipeline.py",
        "if not is_product_learning_eligible(evidence_source):",
    )
    violations = scan_diff_for_security_regressions(d)
    assert any(v["code"] == "LEARN-01" for v in violations)


def test_blocks_removal_of_mask_email():
    d = _remove_diff(
        "app/services/followup_worker.py",
        'log.info("sent to %s", mask_email(to_email))',
        'log.info("sent")',
    )
    violations = scan_diff_for_security_regressions(d)
    assert any(v["code"] == "GDPR-04" for v in violations)


def test_blocks_removal_of_rate_limit_middleware():
    d = _remove_diff(
        "app/main.py",
        "app.add_middleware(RateLimitMiddleware, ...)",
    )
    violations = scan_diff_for_security_regressions(d)
    assert any(v["code"] == "SEC-11" for v in violations)


# ---------- Refactor allowance ----------

def test_refactor_does_not_false_positive():
    """Moving a safety idiom from one file to another must not fire."""
    diff = (
        "--- a/app/core/deps.py\n+++ b/app/core/deps.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-if hmac.compare_digest(a, b):\n"
        "+if hmac.compare_digest(a, b):  # renamed\n"
    )
    violations = scan_diff_for_security_regressions(diff)
    assert not any(v["code"] == "SEC-08" for v in violations)


# ---------- Sensitive paths ----------

def test_sensitive_path_escalates_to_tier2_not_reject():
    # A clean diff touching a TIER_2 file — allowed but tier-raised.
    d = _diff("app/core/deps.py",
              '# harmless comment update')
    c = _cand(d)
    c.id = 1
    c.patch_risk_tier = 0
    allowed, reason = guard_candidate(c)
    assert allowed is True
    assert "sensitive_paths_escalated" in reason
    assert c.patch_risk_tier == 2


def test_sensitive_path_plus_hard_violation_still_rejects():
    d = _diff("app/core/deps.py",
              'log.info("token is %s", access_token)')
    c = _cand(d)
    allowed, reason = guard_candidate(c)
    assert allowed is False
    assert "GDPR-01" in reason


# ---------- Clean path ----------

def test_clean_diff_passes():
    d = _diff("app/services/nothing_dangerous.py",
              "def add(a, b):\n    return a + b")
    violations = scan_diff_for_security_regressions(d)
    assert violations == []
    allowed, reason = guard_candidate(_cand(d))
    assert allowed is True
    assert reason == "ok"


def test_empty_diff_passes():
    assert scan_diff_for_security_regressions(None) == []
    assert scan_diff_for_security_regressions("") == []
    c = _cand("")
    allowed, _ = guard_candidate(c)
    assert allowed is True
