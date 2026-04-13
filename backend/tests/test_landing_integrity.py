"""
Regression tests for the dashboard landing page (`/`).

These tests run from the backend pytest suite for one reason only: they're
the only place a breaking change to the marketing landing can be caught
before deploy. The constraints:

  1. CSS chunks referenced in the prerendered `index.html` must all exist
     on disk. Stale references (e.g. after a partial rebuild that didn't
     regenerate the prerender) mean the landing ships without CSS, which
     looks like "the page is written without graphics" to a user. This
     was the exact symptom of the 2026-04-11 incident.

  2. The client JS bundle must contain recognizable landing content —
     marketing copy, hero headline, navbar — so we know the component
     is actually being shipped and hydration will render something
     meaningful. The landing is a `"use client"` component with a
     pre-hydration `if (!ok) return null` guard, so SSR HTML is empty
     by design. Bundle-content validation is therefore the only proof
     that the landing will be visible on the client.

  3. The root layout must not wrap `{children}` in a class ErrorBoundary
     that would interfere with the landing's `return null` → rerender
     pattern. This is the regression from 2026-04-11 where we wrapped
     the layout and broke hydration silently.

All three checks are filesystem-based — no server needed, no headless
browser needed. They run in milliseconds and lock in the invariant
forever.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_DASHBOARD = Path("/opt/wishspark/dashboard")
_NEXT_DIR = _DASHBOARD / ".next"
_SERVER_APP = _NEXT_DIR / "server" / "app"
_CHUNKS_DIR = _NEXT_DIR / "static" / "chunks"
_LAYOUT_TSX = _DASHBOARD / "src" / "app" / "layout.tsx"


def _prerendered_index_exists() -> bool:
    return (_SERVER_APP / "index.html").exists()


# ---------------------------------------------------------------------------
# CSS reference integrity
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _prerendered_index_exists(),
    reason="No prerendered index.html — run `cd dashboard && npx next build` first",
)
def test_landing_css_references_are_all_reachable():
    """
    Every `<link rel="stylesheet" href="/_next/static/chunks/<name>.css">`
    in the prerendered landing HTML must correspond to an actual file
    in `.next/static/chunks/`. If not, the landing ships without CSS.

    Note: Next 16.2+ uses alphanumeric+underscore filenames like
    `0neevhl_o1ozu.css` instead of pure hex, so the regex allows both.
    """
    html = (_SERVER_APP / "index.html").read_text()
    css_refs = re.findall(r'/_next/static/chunks/([A-Za-z0-9_\-]+\.css)', html)
    assert css_refs, "no CSS references found in landing HTML — Tailwind not bundled?"

    missing: list[str] = []
    for ref in set(css_refs):
        if not (_CHUNKS_DIR / ref).exists():
            missing.append(ref)
    assert not missing, (
        f"Landing HTML references CSS chunks that do not exist on disk: {missing}. "
        "This is the exact stale-reference bug from 2026-04-11 where the landing "
        "served without Tailwind and looked unstyled. Re-run `npx next build` "
        "and `pm2 restart wishspark-dashboard` together."
    )


@pytest.mark.skipif(
    not _prerendered_index_exists(),
    reason="No prerendered index.html — run `cd dashboard && npx next build` first",
)
def test_landing_tailwind_bundle_not_trivial():
    """The main Tailwind bundle (the large CSS chunk) must be substantial.
    A ~3KB CSS file means Tailwind purge dropped everything — another
    way the landing can appear unstyled."""
    html = (_SERVER_APP / "index.html").read_text()
    css_refs = re.findall(r'/_next/static/chunks/([A-Za-z0-9_\-]+\.css)', html)
    sizes = sorted(
        (_CHUNKS_DIR / ref).stat().st_size for ref in set(css_refs)
        if (_CHUNKS_DIR / ref).exists()
    )
    assert sizes, "no CSS chunks on disk to validate"
    # The largest CSS chunk should be > 50KB. Our Tailwind build is typically
    # 150–200KB; anything under 50KB means the purge ate the utilities.
    assert sizes[-1] > 50_000, (
        f"Largest CSS chunk is only {sizes[-1]} bytes — "
        f"Tailwind purge likely stripped utility classes. All sizes: {sizes}"
    )


# ---------------------------------------------------------------------------
# Client bundle content
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _CHUNKS_DIR.exists(),
    reason="No .next/static/chunks — run `npx next build` first",
)
def test_landing_bundle_contains_marketing_content():
    """
    The landing is a `"use client"` component that renders `null` during
    SSR (OAuth guard). Proof that it will be visible post-hydration is
    that its marketing content exists in one of the client JS chunks.
    If no chunk contains it, the landing will be invisible forever.
    """
    markers = [
        "HedgeSpark",            # brand name — must appear
        "Revenue",               # core positioning
    ]
    found: set[str] = set()
    for chunk in _CHUNKS_DIR.glob("*.js"):
        try:
            content = chunk.read_text(errors="ignore")
        except Exception:
            continue
        for m in markers:
            if m in content:
                found.add(m)
        if len(found) == len(markers):
            break
    missing = [m for m in markers if m not in found]
    assert not missing, (
        f"Landing marketing content missing from all client JS chunks: {missing}. "
        "The landing will render empty after hydration — exactly what we saw "
        "on 2026-04-11."
    )


# ---------------------------------------------------------------------------
# Root layout safety
# ---------------------------------------------------------------------------

def test_root_layout_does_not_wrap_children_in_class_error_boundary():
    """
    On 2026-04-11 we wrapped `{children}` in `<ClientErrorBoundary>`. That
    class component intercepted the landing's SSR-null → post-hydration
    rerender as an error and showed a fallback. The landing looked broken.

    Guard: the root layout must not reintroduce a class ErrorBoundary wrap.
    Installing `window.onerror` via a sibling component (ErrorReporterInstaller)
    is fine — it's non-wrapping.
    """
    src = _LAYOUT_TSX.read_text()

    # Acceptable patterns: sibling installer, plain children.
    # Rejected: ClientErrorBoundary wrap around {children}.
    bad_patterns = [
        r'<ClientErrorBoundary[^>]*>\s*\{?\s*children\s*\}?',
    ]
    for pat in bad_patterns:
        assert not re.search(pat, src), (
            f"Root layout.tsx re-introduced a wrap around children matching {pat}. "
            "This breaks the landing. Install global handlers via a sibling "
            "<ErrorReporterInstaller /> component instead."
        )


def test_root_layout_installs_global_error_reporter():
    """Positive invariant: we should still be capturing window errors via
    the non-wrapping installer pattern."""
    src = _LAYOUT_TSX.read_text()
    assert "ErrorReporterInstaller" in src, (
        "Root layout lost the global error reporter installer. "
        "Frontend errors will no longer reach the self-healing pipeline."
    )


def test_root_layout_has_no_route_level_error_tsx():
    """
    error.tsx at the root of src/app/ catches ANY error from any child route
    and replaces it with a minimal fallback. On 2026-04-11 this caused the
    landing to show a route-level error fallback instead of its actual
    content. Only global-error.tsx (which fires only on root layout crash)
    is allowed at the top level.
    """
    root_error = _DASHBOARD / "src" / "app" / "error.tsx"
    assert not root_error.exists(), (
        "src/app/error.tsx exists. This intercepts landing hydration glitches "
        "and shows a minimalist fallback. Place error boundaries under "
        "specific subroutes (e.g. src/app/app/error.tsx) so they don't affect /."
    )
