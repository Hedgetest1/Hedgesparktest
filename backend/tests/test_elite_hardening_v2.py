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


# ---------------------------------------------------------------------------
# 7. Money columns use Numeric/Decimal, never Float
# ---------------------------------------------------------------------------

# SQLAlchemy column-name substrings that indicate a monetary field.
# Hit any of these → the column MUST be Numeric / Integer (cents) /
# Decimal, never Float / REAL / DOUBLE PRECISION. A float cent is a
# silent rounding-loss bug that only surfaces at reconciliation time.
_MONEY_SUBSTRINGS = (
    "_price",
    "price_",
    "_amount",
    "amount_",
    "_revenue",
    "revenue_",
    "_cost",
    "cost_",
    "_fee",
    "fee_",
    "_gross",
    "gross_",
    "_net_sales",
    "_subtotal",
    "subtotal_",
    "_refund",
    "refund_",
    "_charge",
    "charge_",
    "monthly_target",
)

# Column-name suffixes that look monetary but are NOT:
#   * `_pct` / `_percentage` — percentages (floats are fine)
#   * `_mb` / `_gb` — byte sizes
#   * `_ms` / `_seconds` — durations
_NON_MONEY_SUFFIXES = (
    "_pct",
    "_percentage",
    "_mb",
    "_gb",
    "_ms",
    "_seconds",
)

_FLOATY_COLUMN_TYPES = ("Float", "REAL", "DOUBLE PRECISION", "sa.Float")

# Known technical debt — float money columns that exist today and
# cannot be migrated without a schema change (ALTER COLUMN TYPE on a
# live table with existing data). Each entry is a row of debt.
# Adding to this list REQUIRES a founder-approved TIER_2 migration
# plan. Removing an entry means a migration shipped and the column
# is now Numeric — do that before the test can pass on the cleaned
# model.
#
# Format: "<relpath>:<column_name>"
_FLOAT_MONEY_DEBT_ALLOWLIST = {
    "app/models/action_snapshot.py:baseline_revenue_7d",
    "app/models/action_snapshot.py:delta_revenue_7d",
    "app/models/active_nudge.py:estimated_revenue_window",
    "app/models/ad_spend.py:revenue_attributed_eur",
    "app/models/analytics_event.py:revenue_eur",
    "app/models/execution.py:product_b_revenue_24h",
    "app/models/price_watch.py:last_seen_price",
    "app/models/price_watch.py:previous_price",
    "app/models/product_metrics.py:revenue_24h",
    "app/models/scaling_recommendation.py:estimated_cost_increase_eur",
    "app/models/shop_order.py:total_price",
    "app/models/system_snapshot.py:llm_estimated_cost_eur",
    "app/models/trust_contract.py:revenue_delta_eur",
}


def test_money_columns_are_never_float():
    """Every SQLAlchemy column whose name looks monetary must be typed
    as Numeric/Integer/Decimal, never Float. Float cents round silently
    and reconcile wrong — the class of bug that only surfaces when a
    merchant asks 'why is this invoice off by 3 cents'.

    Known debt: a frozen allowlist (`_FLOAT_MONEY_DEBT_ALLOWLIST`)
    captures the pre-existing float money columns that require a
    TIER_2 schema migration to fix. This test is strict on NEW
    additions — any new float money column fails immediately —
    while preserving honest visibility of the open debt.
    """
    models_dir = _BACKEND / "app" / "models"
    if not models_dir.exists():
        pytest.skip("no app/models directory")

    new_violations: list[str] = []
    unknown_debt_allowlist: set[str] = set(_FLOAT_MONEY_DEBT_ALLOWLIST)
    for py_file in sorted(models_dir.rglob("*.py")):
        try:
            text = py_file.read_text()
            tree = ast.parse(text)
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            col_name = node.targets[0].id
            lower = col_name.lower()
            if not any(sub in lower for sub in _MONEY_SUBSTRINGS):
                continue
            if any(lower.endswith(sfx) for sfx in _NON_MONEY_SUFFIXES):
                continue
            if not isinstance(node.value, ast.Call):
                continue
            call_src = ast.unparse(node.value)
            if "Column(" not in call_src and "mapped_column(" not in call_src:
                continue
            if not any(ft in call_src for ft in _FLOATY_COLUMN_TYPES):
                continue
            rel = py_file.relative_to(_BACKEND).as_posix()
            key = f"{rel}:{col_name}"
            if key in _FLOAT_MONEY_DEBT_ALLOWLIST:
                unknown_debt_allowlist.discard(key)
                continue
            new_violations.append(f"{rel}:{node.lineno}  {col_name}")

    assert not new_violations, (
        f"{len(new_violations)} NEW float money column(s) — add "
        f"Numeric(18, 2) or Integer (cents):\n  "
        + "\n  ".join(new_violations[:20])
    )
    assert not unknown_debt_allowlist, (
        f"float-money debt allowlist is stale — these entries no "
        f"longer exist or no longer match the pattern, clean them "
        f"out:\n  "
        + "\n  ".join(sorted(unknown_debt_allowlist))
    )


# ---------------------------------------------------------------------------
# 8. Every webhook route verifies an HMAC signature
# ---------------------------------------------------------------------------

# Files that host webhook ingestion routes. Every POST route in these
# files must call a signature-verification helper before touching
# the request body.
_WEBHOOK_ROUTE_FILES = (
    "app/api/webhooks.py",        # Shopify webhooks
    "app/api/telegram_webhook.py",  # Telegram bot commands
    "app/api/resend_webhooks.py",   # Resend email deliverability
    "app/api/shopify_refunds.py",   # Shopify refund webhook
)

# Names of signature-verification helpers used in this codebase.
# A webhook POST route must reach one of these by its own body or via
# a 1-step delegation (calling a helper defined in the same file that
# itself calls one of these).
_SIGNATURE_HELPERS = (
    "_verify_hmac",
    "_verify_shopify_hmac",
    "_verify_telegram_signature",
    "_verify_webhook",
    "verify_webhook_signature",
    "verify_signature",
    "verify_hmac",
    "hmac.compare_digest",
)


def _function_defs(tree: ast.AST) -> dict[str, ast.AST]:
    """Return {function_name: function_node} for every def in `tree`."""
    out: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            out[node.name] = node
    return out


def _body_contains_verify(node: ast.AST) -> bool:
    src = ast.unparse(node)
    return any(helper in src for helper in _SIGNATURE_HELPERS)


def _called_names(fn_node: ast.AST) -> set[str]:
    """Return the set of callable names referenced in this function
    (both bare `foo()` and attribute `obj.foo()` forms)."""
    names: set[str] = set()
    for node in ast.walk(fn_node):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def test_every_webhook_route_verifies_signature():
    """Every `@router.post(...)` route in a webhook file must reach an
    HMAC/signature verification helper — either directly in its own
    body or through a 1-step delegation to another function defined
    in the same file. A webhook intake that skips signature
    verification is a direct forgery surface.
    """
    violations: list[str] = []
    for rel in _WEBHOOK_ROUTE_FILES:
        path = _BACKEND / rel
        if not path.exists():
            continue
        try:
            text = path.read_text()
            tree = ast.parse(text)
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue

        fn_index = _function_defs(tree)

        for node in fn_index.values():
            is_post = False
            for dec in node.decorator_list:
                dec_src = ast.unparse(dec)
                if re.search(r"\brouter\.post\b|\bapp\.post\b", dec_src):
                    is_post = True
                    break
            if not is_post:
                continue

            # Direct verify in route body
            if _body_contains_verify(node):
                continue

            # 1-step delegation: any helper called from the route body
            # whose own body contains a verify call
            delegated = False
            for called_name in _called_names(node):
                helper = fn_index.get(called_name)
                if helper is not None and _body_contains_verify(helper):
                    delegated = True
                    break
            if delegated:
                continue

            violations.append(f"{rel}:{node.lineno}  {node.name}()")

    assert not violations, (
        f"{len(violations)} webhook POST route(s) do not verify an HMAC "
        f"signature (checked direct body + 1-step delegation):\n  "
        + "\n  ".join(violations)
    )


# ---------------------------------------------------------------------------
# 9. No dangerous code-injection surfaces in app/
# ---------------------------------------------------------------------------

# Function calls that can execute attacker-controlled strings. The
# test uses AST (not regex) so it correctly distinguishes:
#   * bare `eval(...)`            — Python eval, dangerous
#   * `client.eval(...)`          — Redis Lua eval via redis-py, SAFE
#   * `__import__("literal")`     — lazy stdlib import, SAFE
#   * `__import__(user_input)`    — dynamic import, dangerous
#   * `subprocess.run(..., shell=True)` — shell injection, dangerous
#   * `subprocess.run([...])`     — arg list, SAFE
_DANGEROUS_AST_NAMES = {
    "eval",
    "exec",
    "compile",
}

# Explicit allowlist: files where a dangerous call is deliberate + sandboxed.
_DANGEROUS_CALL_ALLOWLIST = {
    # sandbox_executor runs candidate patches in an isolated subprocess by
    # design — it is the one place subprocess invocation is OK.
    "app/sandbox/sandbox_executor.py",
}


def _is_dynamic_import(call: ast.Call) -> bool:
    """True if this is a dynamic `__import__(variable)` with a non-literal
    argument. `__import__("literal")` is treated as safe (equivalent to
    a plain `import` statement used for lazy loading to avoid circulars)."""
    if not (isinstance(call.func, ast.Name) and call.func.id == "__import__"):
        return False
    if not call.args:
        return False
    return not isinstance(call.args[0], ast.Constant)


def _is_shell_true_subprocess(call: ast.Call) -> bool:
    """True if this is `subprocess.<anything>(..., shell=True)`."""
    if not isinstance(call.func, ast.Attribute):
        return False
    if not isinstance(call.func.value, ast.Name) or call.func.value.id != "subprocess":
        return False
    for kw in call.keywords:
        if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
            return True
    return False


def _is_bare_eval_or_exec(call: ast.Call) -> bool:
    """True if this is a bare `eval(...)` or `exec(...)` call (Python).
    Receiver-based calls like `client.eval(...)` (Redis Lua) are NOT
    dangerous and return False."""
    if not isinstance(call.func, ast.Name):
        return False
    return call.func.id in _DANGEROUS_AST_NAMES


def _is_pickle_load(call: ast.Call) -> bool:
    if not isinstance(call.func, ast.Attribute):
        return False
    if call.func.attr not in ("loads", "load"):
        return False
    recv = call.func.value
    return isinstance(recv, ast.Name) and recv.id == "pickle"


def _is_os_system(call: ast.Call) -> bool:
    if not isinstance(call.func, ast.Attribute):
        return False
    if call.func.attr != "system":
        return False
    recv = call.func.value
    return isinstance(recv, ast.Name) and recv.id == "os"


def test_no_dangerous_code_injection_surfaces():
    """`eval(...)`, `exec(...)`, `compile(...)`, `os.system(...)`,
    `pickle.load[s](...)`, `__import__(<variable>)` and
    `subprocess.*(..., shell=True)` are all runtime-string-execution
    primitives. Every occurrence in app/ must live in the explicit
    allowlist or be removed. This is a standing §9.3 security
    invariant.

    Uses AST so Redis Lua `client.eval(...)` and the `__import__(
    "literal")` lazy-import pattern do NOT trip the check.
    """
    app_dir = _BACKEND / "app"
    hits: list[str] = []
    for py_file in sorted(app_dir.rglob("*.py")):
        rel = py_file.relative_to(_BACKEND).as_posix()
        if rel in _DANGEROUS_CALL_ALLOWLIST:
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            label: str | None = None
            if _is_bare_eval_or_exec(node):
                label = f"bare {node.func.id}()"  # type: ignore[attr-defined]
            elif _is_pickle_load(node):
                label = "pickle.load[s]()"
            elif _is_os_system(node):
                label = "os.system()"
            elif _is_dynamic_import(node):
                label = "__import__(<variable>)"
            elif _is_shell_true_subprocess(node):
                label = "subprocess shell=True"
            if label:
                hits.append(f"{rel}:{node.lineno}  [{label}]")

    assert not hits, (
        f"{len(hits)} dangerous code-injection surface(s) in app/:\n  "
        + "\n  ".join(hits[:20])
    )


# ---------------------------------------------------------------------------
# 10. Every migration has a real downgrade() body
# ---------------------------------------------------------------------------

def test_every_migration_has_downgrade():
    """Alembic migrations must be reversible. A `downgrade()` that only
    contains `pass` means we cannot roll back a bad deploy — which is
    the one time rollback actually matters. This test asserts every
    `downgrade()` function body does at least ONE operation (a statement
    that is not a docstring and not a bare `pass`).
    """
    mig_dir = _BACKEND / "migrations" / "versions"
    if not mig_dir.exists():
        pytest.skip(f"no migrations directory at {mig_dir}")

    def _body_is_empty(fn: ast.FunctionDef) -> bool:
        body = fn.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body = body[1:]
        return all(isinstance(s, ast.Pass) for s in body) or not body

    empty: list[str] = []
    checked = 0
    for py_file in sorted(mig_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue

        upgrade_fn: ast.FunctionDef | None = None
        downgrade_fn: ast.FunctionDef | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                if node.name == "upgrade":
                    upgrade_fn = node
                elif node.name == "downgrade":
                    downgrade_fn = node

        if downgrade_fn is None:
            continue
        checked += 1

        # Merge migrations (upgrade() is also empty) are allowed to
        # have an empty downgrade — they're pure alembic graph joins
        # with no schema operations.
        if upgrade_fn is not None and _body_is_empty(upgrade_fn):
            continue

        if _body_is_empty(downgrade_fn):
            empty.append(py_file.name)

    assert not empty, (
        f"{len(empty)} of {checked} migrations have an empty downgrade() — "
        f"un-rollbackable deploys:\n  "
        + "\n  ".join(empty[:20])
    )


# ---------------------------------------------------------------------------
# 11. No obvious secret literal in source
# ---------------------------------------------------------------------------

# Patterns that match common API key / token / private key formats.
# Every hit is a candidate secret leak. False positives go in the
# allowlist below.
_SECRET_PATTERNS = (
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "OpenAI key"),
    (re.compile(r"sk-ant-[A-Za-z0-9-_]{20,}"), "Anthropic key"),
    (re.compile(r"pk_live_[A-Za-z0-9]{24,}"), "Stripe live publishable"),
    (re.compile(r"sk_live_[A-Za-z0-9]{24,}"), "Stripe live secret"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), "private key block"),
    (re.compile(r"ghp_[A-Za-z0-9]{30,}"), "GitHub PAT"),
)

# Root paths to scan. We skip node_modules / venv / .next / .git etc.
_SECRET_SCAN_ROOTS = (
    Path("/opt/wishspark/backend/app"),
    Path("/opt/wishspark/backend/scripts"),
    Path("/opt/wishspark/backend/tests"),
    Path("/opt/wishspark/dashboard/src"),
    Path("/opt/wishspark/tracker"),
)

# Files where a pattern is intentional (test fixtures, regex patterns
# that themselves detect secrets, etc.)
_SECRET_ALLOWLIST = {
    # This file — it contains the detection regexes themselves.
    "backend/tests/test_elite_hardening_v2.py",
    # PII guard — contains intentional regex patterns for secrets.
    "backend/app/core/llm_pii_guard.py",
    # PII guard test suite — contains intentional fake key literals as
    # test fixtures ("sk-ABCDE...", "sk-ant-api03-DEADBEEF..."). The
    # PII guard's own test CANNOT run without these.
    "backend/tests/test_llm_pii_guard.py",
    # Security preflight guard test — contains a fake "sk-1234..."
    # literal as an adversarial input fixture.
    "backend/tests/test_security_preflight_guard.py",
}


def test_no_secret_literal_in_source():
    """No live-format secret string in committed source. Catches the
    class where a developer pastes `sk-...` or `-----BEGIN PRIVATE
    KEY-----` into source while debugging and forgets to remove it.

    Only scans source file types (.py, .ts, .tsx, .js, .css, .html).
    Ignores generated artifacts (.next, node_modules, venv, dist).
    """
    hits: list[str] = []
    for root in _SECRET_SCAN_ROOTS:
        if not root.exists():
            continue
        for file in root.rglob("*"):
            if not file.is_file():
                continue
            if file.suffix not in {".py", ".ts", ".tsx", ".js", ".css", ".html", ".md"}:
                continue
            try:
                rel = file.relative_to(Path("/opt/wishspark")).as_posix()
            except ValueError:
                continue
            if rel in _SECRET_ALLOWLIST:
                continue
            try:
                text = file.read_text(errors="ignore")
            except OSError:
                continue
            for pattern, label in _SECRET_PATTERNS:
                for match in pattern.finditer(text):
                    lineno = text[: match.start()].count("\n") + 1
                    hits.append(f"{rel}:{lineno}  [{label}]  {match.group(0)[:40]}...")

    assert not hits, (
        f"{len(hits)} suspected secret literal(s) in source:\n  "
        + "\n  ".join(hits[:15])
    )
