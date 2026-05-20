"""
Inverse test for scripts/audit_data_truth.py.

An audit script wired into preflight --strict is only valuable if it
actually BITES when a regression is injected. Without this test, a
future refactor could silently weaken the regex / allowlist the world /
hit a parse error and the gate would pass while the real bug ships.

Strategy: create a throwaway .py file under app/ with a known-bad line,
run the script with --strict via subprocess, assert exit 1, then delete
the file. Uses a subprocess so we exercise the real CLI entry point the
preflight hook invokes.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
APP_DIR = BACKEND / "app"
AUDIT_SCRIPT = BACKEND / "scripts" / "audit_data_truth.py"


def _run_audit(strict: bool) -> tuple[int, str]:
    """Invoke the audit script via subprocess and return (returncode, combined output)."""
    cmd = [sys.executable, str(AUDIT_SCRIPT)]
    if strict:
        cmd.append("--strict")
    proc = subprocess.run(cmd, cwd=str(BACKEND), capture_output=True, text=True, timeout=30)
    return proc.returncode, proc.stdout + proc.stderr


def test_audit_strict_passes_on_clean_tree():
    """Baseline: the current tree is clean and --strict exits 0."""
    code, out = _run_audit(strict=True)
    assert code == 0, f"audit_data_truth --strict exited {code} on clean tree:\n{out}"


@pytest.fixture
def injected_bad_file():
    """Drop a throwaway .py file under app/ with a critical regression,
    yield its path, and clean up no matter what.
    """
    path = APP_DIR / "services" / "_test_regression_probe_DELETE_ME.py"
    path.write_text(
        '"""Throwaway fixture file for test_audit_data_truth_gate."""\n'
        'from sqlalchemy import text\n\n'
        'def bad_query(db, shop):\n'
        '    # CRITICAL regression: SUM(total_price) without any currency filter\n'
        '    # anywhere in the query. The audit must catch this.\n'
        '    return db.execute(text("""\n'
        '        SELECT SUM(total_price) AS total_revenue\n'
        '        FROM shop_orders\n'
        '        WHERE shop_domain = :shop\n'
        '          AND created_at >= NOW() - INTERVAL \'30 days\'\n'
        '    """), {"shop": shop}).scalar()\n'
    )
    yield path
    if path.exists():
        path.unlink()


def test_audit_strict_bites_on_sum_without_currency(injected_bad_file):
    """
    Lock-in: inject a SUM(total_price) with no currency filter ANYWHERE
    in the surrounding SQL block. `--strict` must exit 1 and name the
    file in the output. If this test ever starts passing under a weaker
    regex, the preflight gate has become theater.
    """
    code, out = _run_audit(strict=True)
    assert code == 1, (
        f"audit_data_truth failed to detect injected regression "
        f"(exit={code}). Output:\n{out}"
    )
    assert str(injected_bad_file.name) in out, (
        f"output does not mention the bad file {injected_bad_file.name}:\n{out}"
    )
    assert "money_aggregation_no_currency" in out, (
        f"output does not classify as money_aggregation_no_currency:\n{out}"
    )


@pytest.fixture
def injected_hardcoded_euro():
    """Drop a file with a hardcoded € symbol outside any allowlisted context."""
    path = APP_DIR / "services" / "_test_hardcoded_eur_DELETE_ME.py"
    path.write_text(
        '"""Throwaway fixture file for test_audit_data_truth_gate."""\n\n'
        'def fake_render(amount):\n'
        '    return f"\u20ac{amount:,.0f}/mo at risk"\n'
    )
    yield path
    if path.exists():
        path.unlink()


def test_audit_bites_on_hardcoded_euro_symbol(injected_hardcoded_euro):
    """
    The 'hardcoded_currency' check must flag an inline € outside the
    allowlisted detection files. Severity was bumped warning → critical
    on 2026-05-20 after the 45-finding backlog closed (codebase at 0
    findings), so --strict now BLOCKS on new violations — this test
    flipped from `code == 0` to `code == 1`.
    """
    code, out = _run_audit(strict=True)
    # Post-2026-05-20: critical → strict exit 1.
    assert code == 1, (
        f"Strict gate must block on hardcoded € (post-severity-bump). "
        f"Exit code {code}, output:\n{out[:1500]}"
    )
    assert str(injected_hardcoded_euro.name) in out, (
        f"hardcoded_currency finding should be surfaced:\n{out}"
    )
    assert "hardcoded_currency" in out


@pytest.fixture
def injected_frontend_hardcoded_euro(tmp_path):
    """Inject a fake .tsx file under dashboard/src/ with hardcoded €."""
    from pathlib import Path as _Path
    dashboard_src = _Path("/opt/wishspark/dashboard/src")
    if not dashboard_src.exists():
        pytest.skip("dashboard/src not present in this checkout")
    path = dashboard_src / "app" / "_DELETE_ME_probe.tsx"
    path.write_text(
        "// Throwaway fixture file for frontend audit test\n"
        "export function Demo() {\n"
        "  return <span>€42 hardcoded</span>;\n"
        "}\n"
    )
    yield path
    if path.exists():
        path.unlink()


def test_audit_flags_frontend_hardcoded_currency(injected_frontend_hardcoded_euro):
    """
    The frontend scan must catch a hardcoded € in dashboard TSX.
    Severity was bumped warning → critical on 2026-05-20 after the
    24-site frontend sweep closed (codebase at 0 findings); --strict
    now blocks on new violations.
    """
    code, out = _run_audit(strict=True)
    assert code == 1, (
        f"Strict gate must block on frontend hardcoded €. Exit {code}, "
        f"out:\n{out[:1500]}"
    )
    assert injected_frontend_hardcoded_euro.name in out, (
        "frontend_hardcoded_currency must report injected file:\n"
        + out[:1000]
    )
    assert "frontend_hardcoded_currency" in out


def test_strict_gate_blocks_on_hardcoded_currency_post_2026_05_20():
    """Forward-preventer pin: after the 45-finding backlog closed and
    severity was bumped warning → critical, ANY new hardcoded currency
    literal must block the commit. This test exists so a future revert
    of the severity bump (back to "warning") fires immediately — the
    `severity="critical"` line is now load-bearing for regression
    prevention, not just diagnostic.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_audit_data_truth_severity_test", str(AUDIT_SCRIPT),
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_audit_data_truth_severity_test"] = mod
    spec.loader.exec_module(mod)
    # Inspect the source for the critical-severity bump on both backend
    # and frontend currency checks. Structural pin so a revert is
    # mechanically caught (a behavioural test could regress silently
    # if the test fixture path changes).
    src = AUDIT_SCRIPT.read_text()
    # Count `severity="critical"` lines next to hardcoded_currency /
    # frontend_hardcoded_currency findings.
    assert src.count('check="hardcoded_currency"') == 1, (
        "Expected exactly one hardcoded_currency Finding emission"
    )
    assert src.count('check="frontend_hardcoded_currency"') == 1, (
        "Expected exactly one frontend_hardcoded_currency Finding"
    )
    # Both must be at critical severity post 2026-05-20.
    # Window 800 chars: enough to span the multi-line comment that
    # documents the 2026-05-20 severity bump rationale + the
    # severity= line itself.
    backend_block = src[
        src.index('check="hardcoded_currency"'):
        src.index('check="hardcoded_currency"') + 800
    ]
    frontend_block = src[
        src.index('check="frontend_hardcoded_currency"'):
        src.index('check="frontend_hardcoded_currency"') + 800
    ]
    assert 'severity="critical"' in backend_block, (
        "hardcoded_currency severity must be 'critical' post 2026-05-20 "
        "(reverting to 'warning' makes strict gate advisory again, "
        "re-opening the regression vector). Backend block:\n"
        + backend_block
    )
    assert 'severity="critical"' in frontend_block, (
        "frontend_hardcoded_currency severity must be 'critical' "
        "post 2026-05-20. Frontend block:\n" + frontend_block
    )


@pytest.fixture
def injected_div_by_zero(tmp_path):
    """Inject a .py file with an unguarded division by a count-ish var."""
    path = APP_DIR / "services" / "_DELETE_ME_div_probe.py"
    path.write_text(
        '"""Throwaway fixture file for div-by-zero audit test."""\n\n'
        'def bad_metric(rows):\n'
        '    count = len(rows)\n'
        '    # No guard before the divide — count could be 0\n'
        '    avg = sum(r["value"] for r in rows) / count\n'
        '    return avg\n'
    )
    yield path
    if path.exists():
        path.unlink()


def test_audit_flags_division_by_zero(injected_div_by_zero):
    """Unguarded `/ count` should be flagged by the new check."""
    code, out = _run_audit(strict=False)
    assert code == 0  # warning-level does not block
    assert injected_div_by_zero.name in out, (
        f"division_by_zero_unguarded must surface injected file:\n{out[:800]}"
    )
    assert "division_by_zero_unguarded" in out


@pytest.fixture
def injected_guarded_division(tmp_path):
    """Inject a file where the division IS properly guarded. Must NOT fire."""
    path = APP_DIR / "services" / "_DELETE_ME_guarded_probe.py"
    path.write_text(
        '"""Throwaway fixture — guarded division, audit should NOT flag."""\n\n'
        'def good_metric(rows):\n'
        '    count = len(rows)\n'
        '    if count > 0:\n'
        '        return sum(r["value"] for r in rows) / count\n'
        '    return 0\n'
    )
    yield path
    if path.exists():
        path.unlink()


def test_audit_does_not_flag_guarded_division(injected_guarded_division):
    """Locking in the guard detection: `if count > 0` before divide is safe."""
    _, out = _run_audit(strict=False)
    assert injected_guarded_division.name not in out, (
        "Guarded division incorrectly flagged — regex regression:\n"
        + out[:1200]
    )


@pytest.fixture
def injected_stats_claim(tmp_path):
    """Inject a file that renders a lift_pct claim without any significance import."""
    path = APP_DIR / "services" / "_DELETE_ME_claim_probe.py"
    path.write_text(
        '"""Throwaway fixture — marketing claim without significance test."""\n\n'
        'def build_payload(exposed, holdout):\n'
        '    lift_pct = round((exposed - holdout) / holdout * 100, 1) if holdout else 0\n'
        '    return {"message": f"+{lift_pct}% lift measured"}\n'
    )
    yield path
    if path.exists():
        path.unlink()


def test_audit_flags_claim_without_significance(injected_stats_claim):
    """A `+{lift_pct}%` claim in a file without z_test/p_value imports fires."""
    _, out = _run_audit(strict=False)
    assert injected_stats_claim.name in out
    assert "stats_claim_without_significance" in out


def test_audit_script_is_wired_into_preflight():
    """
    Locks in the preflight integration. If a future refactor drops the
    gate step from preflight.sh, this test breaks and flags the
    regression at commit time instead of allowing a drift to ship.
    """
    preflight = (BACKEND / "scripts" / "preflight.sh").read_text()
    assert "audit_data_truth.py" in preflight, (
        "preflight.sh must reference audit_data_truth.py — it is the "
        "currency/timezone/credentials invariant gate"
    )
    assert "--strict" in preflight, "the gate must run in --strict mode"


# ────────────────────────────────────────────────────────────────────────────
# In-source marker opt-out — mutation-sensitive contract tests
# ────────────────────────────────────────────────────────────────────────────
#
# Born 2026-05-20 during the 45-finding backlog close. The legacy
# `_LINE_ALLOWLIST: dict[str, str]` keyed by `"path:lineno"` drifted into
# silent no-op state as line numbers shifted; markers ride the code through
# refactors so the same "documented false positive" stays linked to the
# actual flagged site.
#
# These tests pin the contract:
#   1. A marker within ±10 lines silences the finding.
#   2. Removing the marker re-surfaces the finding (mutation-sensitive).
#   3. A marker beyond ±10 lines does NOT silence the finding (radius pin).
#   4. The legacy `_LINE_ALLOWLIST` is empty — re-populating it is doctrine
#      violation and the migration memo is the place to extend behavior.


@pytest.fixture
def injected_currency_with_marker():
    """File with a hardcoded € + an in-source marker within radius."""
    path = APP_DIR / "services" / "_DELETE_ME_marker_close_probe.py"
    path.write_text(
        '"""Throwaway fixture — marker close to finding (should silence)."""\n\n'
        'def render(amount):\n'
        '    # data-truth-allowed: throwaway test fixture proving the marker silences nearby findings\n'
        '    return f"€{amount:,.0f} stored"\n'
    )
    yield path
    if path.exists():
        path.unlink()


def test_marker_within_radius_silences_finding(injected_currency_with_marker):
    """A `# data-truth-allowed:` marker within ±10 lines of a `€` literal
    must silence the hardcoded_currency finding (otherwise the marker
    migration was vacuous)."""
    _, out = _run_audit(strict=False)
    assert injected_currency_with_marker.name not in out, (
        "Marker within radius should suppress the finding; audit output "
        f"unexpectedly mentioned the file:\n{out[:1200]}"
    )


@pytest.fixture
def injected_currency_no_marker():
    """Same shape as the silenced fixture but WITHOUT the marker — must fire.

    This is the mutation half of the contract: strip the marker line and
    confirm the audit catches the regression. If both files yielded the
    same audit verdict, the marker mechanism would be vacuous."""
    path = APP_DIR / "services" / "_DELETE_ME_marker_missing_probe.py"
    path.write_text(
        '"""Throwaway fixture — NO marker, finding MUST fire (mutation pin)."""\n\n'
        'def render(amount):\n'
        '    # plain comment, no opt-out marker — should NOT silence the audit\n'
        '    return f"€{amount:,.0f} stored"\n'
    )
    yield path
    if path.exists():
        path.unlink()


def test_marker_absent_finding_fires(injected_currency_no_marker):
    """Mutation-sensitive: same shape as the silenced fixture but with the
    marker stripped. The finding MUST appear. This is the proof that
    test_marker_within_radius_silences_finding isn't vacuously passing."""
    _, out = _run_audit(strict=False)
    assert injected_currency_no_marker.name in out, (
        "Marker-absent fixture must surface — proves the silencing test "
        "is not vacuous. Audit output:\n" + out[:1500]
    )


@pytest.fixture
def injected_currency_marker_too_far():
    """Marker placed >10 lines away from the finding — must NOT silence.

    The radius pin: a developer expecting "marker anywhere in the file"
    semantics would write loose markers; without this pin, the radius
    constant could silently drift to "scan whole file" and lose its
    line-locality."""
    path = APP_DIR / "services" / "_DELETE_ME_marker_far_probe.py"
    # 15 blank lines between marker and the flagged content → beyond ±10.
    blank = "\n" * 15
    path.write_text(
        '"""Throwaway fixture — marker too far, finding must fire."""\n\n'
        '# data-truth-allowed: marker intentionally beyond radius to prove locality\n'
        + blank +
        'def render(amount):\n'
        '    return f"€{amount:,.0f} stored"\n'
    )
    yield path
    if path.exists():
        path.unlink()


def test_marker_beyond_radius_does_not_silence(injected_currency_marker_too_far):
    """Marker placed >10 lines from the finding MUST NOT silence it.
    This pins the radius to local-context semantics; a global marker is
    a doctrine violation (use `_FILE_ALLOWLIST` for whole-file opt-out)."""
    _, out = _run_audit(strict=False)
    assert injected_currency_marker_too_far.name in out, (
        "Marker beyond radius must NOT silence the finding — radius pin "
        "broken. Audit output:\n" + out[:1500]
    )


def test_legacy_line_allowlist_is_empty():
    """The legacy `_LINE_ALLOWLIST: dict[str, str]` was migrated to in-source
    markers on 2026-05-20. Re-populating it would re-introduce the silent
    drift class (line keys go stale on refactors). New opt-outs MUST use
    the `# data-truth-allowed: <reason>` marker."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_audit_data_truth_under_test", str(AUDIT_SCRIPT),
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_audit_data_truth_under_test"] = mod
    spec.loader.exec_module(mod)
    assert mod._LINE_ALLOWLIST == {}, (
        "_LINE_ALLOWLIST must stay empty (migrated to in-source markers "
        "2026-05-20). Add a `# data-truth-allowed: <reason>` marker near "
        "the flagged site instead. Re-populating this dict re-introduces "
        f"the silent drift class. Current contents: {mod._LINE_ALLOWLIST}"
    )


@pytest.fixture
def injected_wrapped_payload_fallback():
    """File with `_safe_str(payload.get("currency")) or "USD"` — the shape
    that ships in order_ingestion.py (TIER_2 — not annotatable). The
    auto-suppressor `_WRAPPED_PAYLOAD_FALLBACK_RE` must absorb it without
    requiring an in-source marker."""
    path = APP_DIR / "services" / "_DELETE_ME_wrapped_payload_probe.py"
    path.write_text(
        '"""Throwaway fixture — wrapped payload.get pattern."""\n\n'
        'def _safe_str(x):\n'
        '    return str(x) if x else None\n\n'
        'def ingest(payload):\n'
        '    currency = _safe_str(payload.get("currency")) or "USD"\n'
        '    return currency\n'
    )
    yield path
    if path.exists():
        path.unlink()


def test_wrapper_around_payload_get_auto_suppressed(injected_wrapped_payload_fallback):
    """`_safe_str(payload.get("currency")) or "USD"` shape (ingestion-
    layer defensive fallback) is absorbed by the auto-suppressor without
    needing an in-source marker. This protects TIER_2 files like
    `order_ingestion.py` from needing comment-line edits."""
    _, out = _run_audit(strict=False)
    assert injected_wrapped_payload_fallback.name not in out, (
        "Wrapper-around-payload.get fallback should be auto-suppressed; "
        "audit unexpectedly flagged the fixture file:\n" + out[:1500]
    )


def test_marker_regex_does_not_require_comment_prefix():
    """Multi-line `{/* ... */}` JSX block comments do not place `*` on every
    continuation line; requiring `(?:#|//|/\\*|\\*)` before the marker
    would silently skip JSX continuations. This pins the prefix-free
    contract — the token `data-truth-allowed:` alone is the assertion."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_audit_data_truth_prefix_test", str(AUDIT_SCRIPT),
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_audit_data_truth_prefix_test"] = mod
    spec.loader.exec_module(mod)
    # No comment prefix — bare token on a JSX continuation line.
    bare_token = "    data-truth-allowed: JSX continuation, no leading * here"
    assert mod._MARKER_RE.search(bare_token), (
        f"Marker regex must match a prefix-free token (JSX continuation "
        f"line case). Pattern: {mod._MARKER_RE.pattern!r}"
    )
