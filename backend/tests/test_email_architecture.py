"""
test_email_architecture.py — Regression tests for single-brain email architecture.

These tests PROTECT the invariant:
    "Every email sent by the system must pass through the orchestrator."

If any test here fails, the architecture has been compromised.
"""
import ast
import os
from pathlib import Path

# Derive repo root from this file's location for CI portability.
_REPO_ROOT = Path(os.environ.get("REPO_ROOT", Path(__file__).parent.parent.parent))
_BACKEND = _REPO_ROOT / "backend"


# ---------------------------------------------------------------------------
# Test 1: No service calls send_email() directly
# ---------------------------------------------------------------------------

def test_no_direct_send_email_in_services():
    """
    INVARIANT: Only email_orchestrator.py may call send_email() in app/services/.

    Any other service calling send_email() directly is a bypass.
    """
    violations = []
    services_dir = str(_BACKEND / "app" / "services")

    for fname in os.listdir(services_dir):
        if not fname.endswith(".py"):
            continue
        # The orchestrator is the ONLY allowed caller in services
        if fname == "email_orchestrator.py":
            continue

        path = os.path.join(services_dir, fname)
        with open(path) as f:
            try:
                tree = ast.parse(f.read())
            except SyntaxError:
                continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = None
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name == "send_email":
                    violations.append(f"{fname}:{node.lineno}")

    assert violations == [], (
        f"ARCHITECTURE VIOLATION: send_email() called directly in services: {violations}. "
        f"All email sends must go through the orchestrator (submit_intent or send_immediate)."
    )


# ---------------------------------------------------------------------------
# Test 2: No direct send_email imports in services (except orchestrator)
# ---------------------------------------------------------------------------

def test_no_send_email_import_in_services():
    """
    INVARIANT: No service may import send_email from app.core.email.

    Only the orchestrator is allowed to import it.
    """
    violations = []
    services_dir = str(_BACKEND / "app" / "services")

    for fname in os.listdir(services_dir):
        if not fname.endswith(".py"):
            continue
        if fname == "email_orchestrator.py":
            continue

        path = os.path.join(services_dir, fname)
        with open(path) as f:
            content = f.read()

        if "from app.core.email import send_email" in content:
            violations.append(fname)

    assert violations == [], (
        f"ARCHITECTURE VIOLATION: send_email imported in: {violations}. "
        f"Only email_orchestrator.py may import send_email."
    )


# ---------------------------------------------------------------------------
# Test 3: No orchestrated parameter exists (dead code indicator)
# ---------------------------------------------------------------------------

def test_no_orchestrated_parameter():
    """
    INVARIANT: The 'orchestrated' parameter has been removed.

    Its existence means a direct-send bypass path could be reactivated.
    """
    files_to_check = [
        str(_BACKEND / "app" / "services" / "revenue_triggers.py"),
        str(_BACKEND / "app" / "services" / "silence_detector.py"),
        str(_BACKEND / "app" / "services" / "merchant_digest.py"),
    ]

    violations = []
    for path in files_to_check:
        with open(path) as f:
            content = f.read()
        if "orchestrated" in content:
            violations.append(os.path.basename(path))

    assert violations == [], (
        f"DEAD CODE: 'orchestrated' parameter still exists in: {violations}. "
        f"All producers must route through orchestrator unconditionally."
    )


# ---------------------------------------------------------------------------
# Test 4: Governance registry covers all email types
# ---------------------------------------------------------------------------

def test_governance_registry_complete():
    """All email types used by producers must be registered in governance."""
    from app.services.email_governance import TEMPLATE_REGISTRY

    required_types = {
        "welcome", "beta_welcome", "setup_incomplete", "first_insight",
        "connection_issue", "followup_opened", "followup_clicked",
        "followup_noopen", "weekly_digest", "reengagement",
        "trigger_high_intent_leak", "trigger_traffic_spike",
        "trigger_return_visitor_surge", "auto_response",
    }

    registered = set(TEMPLATE_REGISTRY.keys())
    missing = required_types - registered
    assert missing == set(), f"GOVERNANCE GAP: email types not in registry: {missing}"


# ---------------------------------------------------------------------------
# Test 5: Every registered template has a valid sender
# ---------------------------------------------------------------------------

def test_sender_identity_defined():
    """Every template in the registry must have a sender in the identity rules."""
    from app.services.email_governance import TEMPLATE_REGISTRY, IDENTITY_RULES

    valid_senders = set(IDENTITY_RULES.keys())
    violations = []

    for name, entry in TEMPLATE_REGISTRY.items():
        sender = entry.get("sender")
        if sender not in valid_senders:
            violations.append(f"{name}: sender={sender}")

    assert violations == [], f"IDENTITY GAP: templates with unknown senders: {violations}"


# ---------------------------------------------------------------------------
# Test 6: Template baselines exist for all registered templates
# ---------------------------------------------------------------------------

def test_template_baselines_complete():
    """All templates that use _wrap_html should have a drift baseline."""
    from app.services.email_governance import TEMPLATE_REGISTRY, _TEMPLATE_BASELINES

    should_have_baseline = {
        name for name, entry in TEMPLATE_REGISTRY.items()
        if entry.get("uses_wrap_html") and name in {
            "welcome", "beta_welcome", "setup_incomplete", "first_insight",
            "connection_issue", "followup_opened", "followup_clicked", "followup_noopen",
        }
    }

    missing = should_have_baseline - set(_TEMPLATE_BASELINES.keys())
    assert missing == set(), f"DRIFT DETECTION GAP: templates without baselines: {missing}"


# ---------------------------------------------------------------------------
# Test 7: send_email has caller enforcement
# ---------------------------------------------------------------------------

def test_send_email_has_caller_enforcement():
    """send_email() must contain caller enforcement code."""
    path = str(_BACKEND / "app" / "core" / "email.py")
    with open(path) as f:
        content = f.read()

    assert "UNAUTHORIZED CALLER" in content, (
        "ENFORCEMENT MISSING: send_email() must block unauthorized callers."
    )
    assert "_ALLOWED_CALLERS" in content, (
        "ENFORCEMENT MISSING: send_email() must define allowed callers."
    )
