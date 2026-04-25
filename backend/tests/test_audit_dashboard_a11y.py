"""Tests for audit_dashboard_a11y.py — regression pins for the
two false-negative gaps caught by the 2026-04-25 night devil's-
advocate run, plus the icon-only-button detector.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "audit_dashboard_a11y.py"


def _load_audit():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("audit_dashboard_a11y", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def audit():
    return _load_audit()


# ---------------------------------------------------------------------------
# Pattern 1 — icon-only buttons
# ---------------------------------------------------------------------------

def test_icon_only_button_without_aria_label_is_flagged(audit, tmp_path):
    f = tmp_path / "X.tsx"
    f.write_text(
        '''export function X() {
  return (
    <button onClick={close}>
      <svg viewBox="0 0 24 24"><line x1="0" y1="0" x2="1" y2="1" /></svg>
    </button>
  );
}
'''
    )
    findings = audit.find_icon_only_buttons(f)
    assert len(findings) == 1


def test_icon_only_button_with_aria_label_passes(audit, tmp_path):
    f = tmp_path / "X.tsx"
    f.write_text(
        '''export function X() {
  return (
    <button aria-label="Close" onClick={close}>
      <svg viewBox="0 0 24 24"></svg>
    </button>
  );
}
'''
    )
    assert audit.find_icon_only_buttons(f) == []


def test_button_with_visible_text_passes(audit, tmp_path):
    f = tmp_path / "X.tsx"
    f.write_text('<button onClick={x}>Save</button>\n')
    assert audit.find_icon_only_buttons(f) == []


# ---------------------------------------------------------------------------
# Pattern 2 — low-contrast small text (with the 2026-04-25 generalized regex)
# ---------------------------------------------------------------------------

def test_text_10px_slate_500_is_flagged(audit, tmp_path):
    f = tmp_path / "X.tsx"
    f.write_text('<div className="text-[10px] text-slate-500">x</div>\n')
    findings = audit.find_low_contrast_small_text(f)
    assert len(findings) == 1


def test_text_decimal_size_slate_500_is_flagged(audit, tmp_path):
    """REGRESSION: text-[9.5px] was missed by the original hard-coded regex.
    Caught 2026-04-25 night by founder devil's-advocate prompt."""
    f = tmp_path / "X.tsx"
    f.write_text('<div className="text-[9.5px] text-slate-500">x</div>\n')
    findings = audit.find_low_contrast_small_text(f)
    assert len(findings) == 1


def test_text_9px_slate_600_is_flagged(audit, tmp_path):
    """REGRESSION: text-[9px] also missed by the original regex."""
    f = tmp_path / "X.tsx"
    f.write_text('<div className="text-[9px] text-slate-600">x</div>\n')
    findings = audit.find_low_contrast_small_text(f)
    assert len(findings) == 1


def test_text_xs_slate_500_is_flagged(audit, tmp_path):
    f = tmp_path / "X.tsx"
    f.write_text('<div className="text-xs text-slate-500">x</div>\n')
    findings = audit.find_low_contrast_small_text(f)
    assert len(findings) == 1


def test_text_14px_slate_500_is_NOT_flagged(audit, tmp_path):
    """Sweep direction was: only small-font sites got bumped. text-[14px]
    is regular-text territory and slate-500 sometimes passes contrast on
    lighter backgrounds — leave it alone."""
    f = tmp_path / "X.tsx"
    f.write_text('<div className="text-[14px] text-slate-500">x</div>\n')
    assert audit.find_low_contrast_small_text(f) == []


def test_text_slate_400_with_small_font_is_NOT_flagged(audit, tmp_path):
    f = tmp_path / "X.tsx"
    f.write_text('<div className="text-[10px] text-slate-400">x</div>\n')
    assert audit.find_low_contrast_small_text(f) == []


# ---------------------------------------------------------------------------
# Pattern 3 — inline-style low-contrast hex (NEW post-DA gap)
# ---------------------------------------------------------------------------

def test_inline_style_slate_500_hex_is_flagged(audit, tmp_path):
    """REGRESSION: 21 cards used `style={{ color: '#64748b' }}` directly,
    bypassing the className-only scan. Caught 2026-04-25 night."""
    f = tmp_path / "X.tsx"
    f.write_text(
        '<span style={{ color: "#64748b", fontSize: "11px" }}>x</span>\n'
    )
    findings = audit.find_inline_low_contrast(f)
    assert len(findings) == 1


def test_inline_style_slate_600_hex_is_flagged(audit, tmp_path):
    f = tmp_path / "X.tsx"
    f.write_text("<span style={{ color: '#45556c' }}>x</span>\n")
    findings = audit.find_inline_low_contrast(f)
    assert len(findings) == 1


def test_inline_style_slate_400_hex_is_NOT_flagged(audit, tmp_path):
    """slate-400 (#94a3b8) is the safe replacement and must not trigger."""
    f = tmp_path / "X.tsx"
    f.write_text('<span style={{ color: "#94a3b8" }}>x</span>\n')
    assert audit.find_inline_low_contrast(f) == []


def test_inline_style_uppercase_hex_is_flagged(audit, tmp_path):
    """Hex strings are case-insensitive in CSS; the audit must respect that."""
    f = tmp_path / "X.tsx"
    f.write_text('<span style={{ color: "#64748B" }}>x</span>\n')
    findings = audit.find_inline_low_contrast(f)
    assert len(findings) == 1


def test_opacity_modified_slate_500_is_flagged(audit, tmp_path):
    """REGRESSION: 2026-04-25 night Mode 4 pre-mortem — opacity modifiers
    on already-low-contrast tokens always REDUCE contrast on dark bg.
    `text-slate-500/50` would have escaped the original `\\btext-slate-500\\b`
    regex because `/` breaks word boundary. Extended to `(?:/\\d+)?`."""
    f = tmp_path / "X.tsx"
    f.write_text('<div className="text-[10px] text-slate-500/50">x</div>\n')
    findings = audit.find_low_contrast_small_text(f)
    assert len(findings) == 1


def test_opacity_modified_slate_600_is_flagged(audit, tmp_path):
    f = tmp_path / "X.tsx"
    f.write_text('<div className="text-[11px] text-slate-600/80">x</div>\n')
    findings = audit.find_low_contrast_small_text(f)
    assert len(findings) == 1


# ---------------------------------------------------------------------------
# Smoke for the strict mode — clean repo means rc=0
# ---------------------------------------------------------------------------

def test_strict_mode_against_real_dashboard_is_clean(audit):
    """Pin the current 0-finding state. Future regression breaks this test
    BEFORE it can ship via preflight, giving an in-test failure that's
    cheaper to diagnose than a preflight-time block."""
    rc = audit.main(["--strict"])
    assert rc == 0
