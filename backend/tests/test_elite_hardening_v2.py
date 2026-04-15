"""
Elite hardening 2.0 — atomic invariants that lock top-1 contracts.

Every test in this file exists for a specific reason: a class of silent
regression that would ship past every other gate (preflight, smoke,
a11y, bundle, lighthouse) and only surface after a merchant noticed.
Each test is small, fast, hermetic (filesystem or source parsing only,
no DB / no network / no browser), and named after the invariant it
guards. When a test breaks, the failure message points at the exact
fix location — no archaeology required.

These are NOT integration tests. Coverage sprawl is a failure mode;
the invariants here are hand-picked to protect what actually matters
for a 10k-merchant production product.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_DASHBOARD = Path("/opt/wishspark/dashboard")
_BACKEND = Path("/opt/wishspark/backend")
_TRACKER = Path("/opt/wishspark/tracker")
_PRERENDERED_INDEX = _DASHBOARD / ".next" / "server" / "app" / "index.html"


# ---------------------------------------------------------------------------
# 1. Landing hero renders in SSR body — catches the 2026-04-15 SSR regression
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _PRERENDERED_INDEX.exists(),
    reason="no prerendered index.html — run `cd dashboard && npx next build` first",
)
def test_landing_hero_renders_in_ssr_body():
    """The landing HTML served to Google and cold visitors must contain
    the actual hero copy, not a blank `<div hidden>` shell.

    History: on 2026-04-15 the landing shipped an empty `<body>` because
    `useOAuthRedirect()` gated the render on a `useState(false)` flag
    that only flipped inside `useEffect` — which doesn't run server-
    side. The page bundled fine, lighthouse Perf dropped to 0 with
    NO_LCP, and nobody noticed for a day. This test is the static
    regression canary for that class of bug. A full description of the
    fix lives in commit 85c04e1.
    """
    html = _PRERENDERED_INDEX.read_text()
    body_match = re.search(r"<body[^>]*>(.*?)</body>", html, re.DOTALL)
    assert body_match, "prerendered index.html has no <body> tag"
    body = body_match.group(1)

    # Hard floor: a broken body shell is ~40 bytes. A real landing
    # body is ~95 KB. 3 KB gives ~3x headroom over the smallest
    # legitimate page in the tree.
    assert len(body) > 3000, (
        f"landing SSR body is only {len(body)} bytes — the useOAuthRedirect "
        f"gate is likely blocking render again. See commit 85c04e1."
    )

    # Hero copy must appear verbatim in the SSR body. The strings here
    # match the founder-approved hero voice (commit c26da5f + bfa86d7).
    # If these strings drift intentionally, update both places in the
    # same commit.
    expected_phrases = [
        "Your store is leaking money",
        "You don",  # matches both "don't" and "don&#x27;t" (HTML-escaped)
        "know why",
        "We show you where",
    ]
    for phrase in expected_phrases:
        assert phrase in body, (
            f"hero phrase {phrase!r} missing from prerendered landing body. "
            f"Either the copy was edited without rebuild, or a gate-return-null "
            f"regression dropped the hero from SSR."
        )


# ---------------------------------------------------------------------------
# 2. Brand voice coherence — hero and metadata must match
# ---------------------------------------------------------------------------

def test_metadata_and_hero_share_brand_voice():
    """The landing hero, the <meta description>, the Open Graph card,
    the Twitter card and the JSON-LD schema must all tell the same
    brand story. When a founder shares `hedgesparkhq.com` on Slack,
    LinkedIn or Google, the preview snippet and the clicked-through
    page must not contradict each other.

    History: on 2026-04-15 a copy revert touched the landing hero
    but left the metadata on the previous voice (`silent curse` /
    `break the spell`). This test is the canary for that drift.
    """
    page_tsx = (_DASHBOARD / "src/app/page.tsx").read_text()
    layout_tsx = (_DASHBOARD / "src/app/layout.tsx").read_text()
    i18n_ts = (_DASHBOARD / "src/app/lib/i18n.ts").read_text()

    # Strings that MUST appear in the landing hero (page.tsx).
    current_hero = [
        "Your store is leaking money",
        "We show you where",
        "The most advanced dashboard built for Shopify",
    ]
    for phrase in current_hero:
        assert phrase in page_tsx, (
            f"hero phrase {phrase!r} missing from page.tsx — copy drift detected"
        )

    # Strings from a prior voice that MUST NOT resurface anywhere in
    # the user-facing dashboard source (page.tsx, layout.tsx, i18n dict).
    forbidden_phrases = [
        "silent curse",
        "break the spell",
        "Lifts the curse",
        "Proves the magic worked",
    ]
    drift = []
    for phrase in forbidden_phrases:
        if phrase in page_tsx:
            drift.append(f"page.tsx contains {phrase!r}")
        if phrase in layout_tsx:
            drift.append(f"layout.tsx contains {phrase!r}")
        if phrase in i18n_ts:
            drift.append(f"lib/i18n.ts contains {phrase!r}")

    assert not drift, (
        "previous brand voice has resurfaced in user-facing surfaces:\n  "
        + "\n  ".join(drift)
    )

    # Metadata must echo at least one distinctive hero phrase so the
    # social preview and the clicked-through page agree.
    canonical_hook = "leaking money"
    assert canonical_hook in layout_tsx, (
        f"layout.tsx metadata does not mention {canonical_hook!r} — "
        f"social previews will drift from the landing hero"
    )


# ---------------------------------------------------------------------------
# 3. Every Redis SET has a TTL — the single most important 10k-scale
#    invariant (CLAUDE.md §12). An unbounded key = a memory leak that
#    only surfaces under real traffic.
# ---------------------------------------------------------------------------

_REDIS_SET_RE = re.compile(
    r"\b(?:redis|rc|_rc|_redis|redis_client|r_client|self\.redis|self\.rc)\.set\s*\(",
    re.IGNORECASE,
)


def _matching_close_paren(text: str, open_pos: int) -> int:
    """Return the index of the `)` that closes the `(` at `open_pos`."""
    depth = 0
    i = open_pos
    while i < len(text):
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        elif c in ("'", '"'):
            # Skip quoted string
            quote = c
            i += 1
            while i < len(text) and text[i] != quote:
                if text[i] == "\\":
                    i += 1
                i += 1
        i += 1
    return -1


def test_every_redis_set_has_ttl():
    """Every `redis.set(...)` (or `rc.set`, `self.redis.set`, etc.)
    in the backend must carry a TTL — `ex=N`, `px=N`, `exat=`, `pxat=`,
    or the call must actually be `setex(...)`. An untimed SET at 10k
    merchant scale becomes a silent memory leak in Redis.

    Exemption convention: if the `.set(...)` is intentionally
    persistent (audit chain head, GDPR opt-out flag, operator
    standby flag), the call site must carry a `REDIS-PERSIST-OK:`
    comment on the same line or on one of the 5 lines above,
    stating the reason. Any exemption without a written justification
    is a test failure.
    """
    app_dir = _BACKEND / "app"
    violations: list[str] = []
    checked = 0

    for py_file in sorted(app_dir.rglob("*.py")):
        if "_pb2" in py_file.name or "/venv/" in str(py_file):
            continue
        try:
            text = py_file.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        lines = text.splitlines()
        for match in _REDIS_SET_RE.finditer(text):
            checked += 1
            open_paren_pos = match.end() - 1
            close_pos = _matching_close_paren(text, open_paren_pos)
            if close_pos == -1:
                continue
            call_body = text[open_paren_pos : close_pos + 1]
            has_ttl = any(
                kw in call_body for kw in ("ex=", "px=", "exat=", "pxat=", "nx=True, ex=")
            )
            if has_ttl:
                continue
            # Check for the REDIS-PERSIST-OK marker on the same line or
            # the 5 lines above (comment lives with the call).
            lineno = text[: match.start()].count("\n") + 1
            window_start = max(0, lineno - 6)
            window = "\n".join(lines[window_start:lineno])
            if "REDIS-PERSIST-OK" in window:
                continue
            rel = py_file.relative_to(_BACKEND)
            violations.append(f"{rel}:{lineno}  {call_body[:80]}...")

    assert not violations, (
        f"{len(violations)} redis .set() calls without TTL out of {checked} checked. "
        f"Each one is a silent 10k-scale memory leak. Add `ex=<seconds>` "
        f"(or use `setex(...)`) to every site below:\n  "
        + "\n  ".join(violations[:30])
    )


# ---------------------------------------------------------------------------
# 4. No WishSpark brand drift in user-facing surfaces
# ---------------------------------------------------------------------------

# The WishSpark → HedgeSpark rebrand is complete in every human-visible
# surface, but a small set of wire-level identifiers MUST stay on the
# old name forever because they are stable external APIs. Renaming any
# of these would break existing integrations mid-deploy:
#
#   * `__wishsparkInit` / `__wishsparkNudgeInit`  —  boot-guard globals
#     set by cached tracker copies that merchant browsers still hold
#     from before the rebrand. Changing the global name re-runs the
#     boot on top of an already-initialized tracker and duplicates
#     every event emission.
#
#   * `data-wishspark-nudge`  —  DOM attribute on every rendered nudge
#     element, referenced by CSS selectors in the same file. Renaming
#     breaks in-flight nudges that a merchant's storefront has in its
#     DOM during the window between the old and new tracker loading.
#
#   * `WishSpark — High Intent Signal`  —  Klaviyo event name emitted
#     to merchants' Klaviyo accounts. Merchants have existing flows
#     filtering on this exact string; renaming is a breaking change.
#
# Everything else — `[WishSpark]` console messages, JSDoc titles,
# function names, landing copy — is genuine brand drift and must be
# renamed to HedgeSpark.
_BRAND_DRIFT_EXEMPTIONS = (
    # Stable JS globals — cached tracker back-compat
    "__wishsparkInit",
    "__wishsparkNudgeInit",
    # Stable DOM attribute + CSS selector substring
    "data-wishspark-nudge",
    # Klaviyo wire-level event name (shipped into merchants' Klaviyo)
    "WishSpark \u2014 High Intent Signal",  # em-dash variant
    "WishSpark - High Intent Signal",        # ascii-dash variant
    # PM2 process names — not user-visible, internal ops only
    "wishspark-backend",
    "wishspark-dashboard",
    "wishspark-worker",
    "wishspark-agent-worker",
    "wishspark-aggregation-worker",
    "wishspark-segment-monitor",
    "wishspark-nudge-optimizer",
    "wishspark-gdpr-worker",
)


def _strip_exemptions(line: str) -> str:
    """Remove every known-stable identifier from the line before scanning
    for brand drift. What remains is drift iff it still contains
    `WishSpark` or `wishspark`.
    """
    for ex in _BRAND_DRIFT_EXEMPTIONS:
        line = line.replace(ex, "")
    return line


def _has_brand_drift(line: str) -> bool:
    stripped = _strip_exemptions(line)
    return "WishSpark" in stripped or "wishspark" in stripped


def test_no_wishspark_brand_drift_in_user_surfaces():
    """No `WishSpark` / `wishspark` title-case string in code paths a
    real user could see: tracker scripts (run in merchant browsers and
    visible in DevTools), dashboard source, landing copy.

    The `__wishsparkInit` internal boot-guard global is exempted —
    renaming it would re-init old cached trackers and cause duplicate
    event emission. Same for PM2 process names (they're not surfaces,
    they're identifiers).
    """
    surfaces = [
        (_DASHBOARD / "src", {".ts", ".tsx", ".css"}),
        (_TRACKER, {".js"}),
    ]
    hits: list[str] = []
    for root, suffixes in surfaces:
        for file in sorted(root.rglob("*")):
            if not file.is_file() or file.suffix not in suffixes:
                continue
            try:
                text = file.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if _has_brand_drift(line):
                    hits.append(f"{file.relative_to(Path('/opt/wishspark'))}:{lineno}  {line.strip()[:100]}")

    assert not hits, (
        f"{len(hits)} WishSpark brand drift hits in user-facing surfaces:\n  "
        + "\n  ".join(hits[:20])
    )


# ---------------------------------------------------------------------------
# 5. Every LLM call site is wrapped by the PII guard
# ---------------------------------------------------------------------------

# Known entry points to an LLM provider. The call is "raw" unless it
# lives inside a module that already does PII scanning before hitting
# the provider (e.g. app/core/llm_router.py, app/services/llm_*.py).
_LLM_CALL_RE = re.compile(
    r"\b(?:anthropic_client|openai_client|anthropic|openai)\.(?:messages|chat|completions)\b"
)

# Files that ARE the PII-guarded boundary — they scan input BEFORE
# calling the provider, so their internal call sites are the intended
# single entry point for the rest of the codebase.
_PII_GUARD_BOUNDARY_FILES = {
    "app/core/llm_router.py",
    "app/core/llm_pii_guard.py",
    "app/core/llm_safety.py",
    "app/core/llm_budget.py",
}


def test_every_llm_call_site_is_pii_guarded():
    """Every raw LLM provider call (anthropic/openai SDK) must happen
    inside the PII-guard boundary layer. Callers outside that boundary
    must go through `llm_router.complete()` (or an equivalent guarded
    helper), never reach the provider directly.

    Rationale: `app/core/llm_pii_guard.py` is the runtime regex scanner
    that blocks merchant emails, Shopify tokens, API keys and similar
    from leaving our process in an LLM prompt. If a service bypasses
    it, PII escapes — and that's a §9.3 security invariant failure.
    """
    app_dir = _BACKEND / "app"
    violations: list[str] = []
    for py_file in sorted(app_dir.rglob("*.py")):
        rel = py_file.relative_to(_BACKEND).as_posix()
        if rel in _PII_GUARD_BOUNDARY_FILES:
            continue
        try:
            text = py_file.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        for match in _LLM_CALL_RE.finditer(text):
            lineno = text[: match.start()].count("\n") + 1
            violations.append(f"{rel}:{lineno}  {match.group(0)}")

    assert not violations, (
        f"{len(violations)} raw LLM provider calls outside the PII-guard boundary. "
        f"Route every one through `llm_router.complete()` or an equivalent "
        f"guarded helper:\n  "
        + "\n  ".join(violations[:20])
    )


# ---------------------------------------------------------------------------
# 6. Every TIER_2 file declares its tier in a header comment
# ---------------------------------------------------------------------------

# TIER_2 files from CLAUDE.md §10 — the "never modify without explicit
# human approval" list. Every path here must exist AND carry the
# in-file `TIER_2` marker so rename / move / refactor cannot silently
# downgrade a TIER_2 file to TIER_0 without breaking this test.
_TIER_2_FILES = [
    "app/core/token_crypto.py",
    "app/core/merchant_session.py",
    "app/api/shopify_oauth.py",
    "app/api/billing.py",
    "app/core/deps.py",
    "app/api/webhooks.py",
    "app/services/order_ingestion.py",
    "app/services/gdpr_processor.py",
]


def test_every_tier2_file_declares_its_tier():
    """Every TIER_2 file (CLAUDE.md §10) must declare `TIER_2` in its
    top-of-file docstring or header comment. If someone (human, Claude
    session, or the self-healing pipeline) renames or moves one without
    updating the marker, this test fires loud.
    """
    missing: list[str] = []
    unreadable: list[str] = []
    not_marked: list[str] = []
    for rel in _TIER_2_FILES:
        path = _BACKEND / rel
        if not path.exists():
            missing.append(rel)
            continue
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            unreadable.append(rel)
            continue
        header = text[:2000]
        if "TIER_2" not in header:
            not_marked.append(rel)

    problems: list[str] = []
    if missing:
        problems.append(f"missing: {missing}")
    if unreadable:
        problems.append(f"unreadable: {unreadable}")
    if not_marked:
        problems.append(
            f"no `TIER_2` marker in header (first 2 KB):\n  "
            + "\n  ".join(not_marked)
        )

    assert not problems, (
        "TIER_2 file registry is out of sync with CLAUDE.md §10:\n"
        + "\n".join(problems)
    )
