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
import os
import re
from pathlib import Path

import pytest

# Derive repo root from this file's location so tests work both on the production
# server (/opt/wishspark/) and in CI (checked-out repo under /home/runner/work/).
_REPO_ROOT = Path(os.environ.get("REPO_ROOT", Path(__file__).parent.parent.parent))
_DASHBOARD = _REPO_ROOT / "dashboard"
_BACKEND = _REPO_ROOT / "backend"
_TRACKER = _REPO_ROOT / "tracker"
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
    "_spend",
    "spend_",
    "_payout",
    "payout_",
    "_margin_eur",
    "_eur",
    "_usd",
    "_gbp",
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

# Known float-money debt. Empty post-2026-04-15 TIER_2 migration
# `zzz8_float_money_to_numeric` — all 14 previously-Float money
# columns are now NUMERIC(18, 2). Keep this set as the frozen debt
# ledger: any NEW float-money column must either be migrated
# immediately or explicitly added to this allowlist with a founder-
# approved migration plan attached.
_FLOAT_MONEY_DEBT_ALLOWLIST: set[str] = set()


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
    _BACKEND / "app",
    _BACKEND / "scripts",
    _BACKEND / "tests",
    _DASHBOARD / "src",
    _TRACKER,
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
                rel = file.relative_to(_REPO_ROOT).as_posix()
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


# ---------------------------------------------------------------------------
# 12. No new datetime.utcnow in app/ — freeze the 2026-04-15 sweep
# ---------------------------------------------------------------------------
#
# `datetime.utcnow()` is deprecated on Python 3.12+ and scheduled for
# removal. The 2026-04-15 hardening sweep replaced every reference in
# `app/models/*.py` (66 call sites across 32 files) with the shared
# helper `utc_now_naive` from `app/core/time_utils.py`. This test is
# the lock-in: any new `datetime.utcnow` landing in `app/` is a
# regression. Tests are exempt (they use the function liberally as
# fixture data and the deprecation isn't load-bearing there).
#
# The helper produces a semantically identical naive UTC datetime,
# so migrating a new call site is a one-line edit:
#     from app.core.time_utils import utc_now_naive
#     utc_now_naive  # instead of datetime.utcnow
# ---------------------------------------------------------------------------

def test_no_new_datetime_utcnow():
    """Freeze the datetime.utcnow sweep. Any new call site in app/
    must use `utc_now_naive` from `app/core/time_utils.py` instead.

    Uses AST walking, not regex — so f-string interpolation, method
    chains, string literals, and comments are all handled without
    false positives. Scans the full `app/` tree (not just models).
    """
    hits: list[str] = []
    for file in (_BACKEND / "app").rglob("*.py"):
        if "__pycache__" in file.parts:
            continue
        # The helper module documents the deprecated name in its
        # own docstring — legitimate reference, not a call.
        if file.name == "time_utils.py":
            continue
        rel = file.relative_to(_BACKEND).as_posix()
        try:
            source = file.read_text()
        except OSError:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            # Match `datetime.utcnow` as an attribute access, whether
            # called (`datetime.utcnow()`) or passed by name (as a
            # SQLAlchemy column default). `ast.Attribute(value=Name('datetime'), attr='utcnow')`.
            if not isinstance(node, ast.Attribute):
                continue
            if node.attr != "utcnow":
                continue
            if not (isinstance(node.value, ast.Name) and node.value.id == "datetime"):
                continue
            lineno = getattr(node, "lineno", 0)
            line = source.splitlines()[lineno - 1].strip() if lineno else ""
            hits.append(f"{rel}:{lineno}  {line[:90]}")

    assert not hits, (
        f"{len(hits)} new `datetime.utcnow` reference(s) in app/ — "
        f"replace with `utc_now_naive` from `app/core/time_utils.py`:\n  "
        + "\n  ".join(hits[:20])
    )


# ---------------------------------------------------------------------------
# 13. No new raw-SQL f-string interpolation — freeze existing debt
# ---------------------------------------------------------------------------
#
# `text(f"... {var} ...")` is a SQL-injection vector whenever `var`
# flows from user input. The 13 sites below were audited on
# 2026-04-15 and confirmed to interpolate only trusted constants:
# table names from hardcoded loops, column names from whitelist dicts,
# enum-like string literals from ternaries, integer IDs cast via
# `str(int(...))`. They are SAFE in practice but still ugly — they
# are frozen in this allowlist and can be refactored opportunistically
# into prepared-statement dispatch tables. Any NEW site lands as a
# test failure, forcing the author to either parameterize the query
# or consciously add to the debt ledger with a written justification.
#
# Audit notes per site (all safe 2026-04-15):
#   execution_actions.py:384    — where clause from constant strings
#   weekly_digest.py:476        — ":p0/:p1..." placeholders from enumerate
#   evolution_business_outcomes.py:120 — shop filter constant literal
#   utm_attribution.py:108      — "ASC"/"DESC" from ternary
#   simulation_engine.py:317    — table name from hardcoded tuple
#   simulation_engine.py:665    — table name from hardcoded tuple
#   nudge_rank.py:148           — integer IDs from DB rows
#   nudge_rank.py:198           — integer IDs from DB rows
#   evolution_outcomes.py:624   — source filter constant literal
#   execution_engine.py:713     — parameter names from enumerate
#   execution_engine.py:722     — parameter names from enumerate
#   email_performance.py:63     — column from whitelist dict (dict IS the guard)
# ---------------------------------------------------------------------------

_RAW_SQL_FSTRING_PATTERN = re.compile(r'\btext\(\s*f["\']')

_RAW_SQL_FSTRING_ALLOWLIST: set[str] = {
    "app/api/execution_actions.py:384",
    "app/services/weekly_digest.py:476",
    "app/services/utm_attribution.py:111",
    "app/services/nudge_rank.py:148",
    "app/services/nudge_rank.py:200",
    # evolution_outcomes.py:625 — removed: refactored to bind param
    "app/services/execution_engine.py:868",
    "app/services/execution_engine.py:877",
    "app/services/email_performance.py:63",
    # gdpr_processor: where clause built from hardcoded ":cid"/":email"
    # filter strings joined with " OR ". Parameters are bound via the
    # second arg to execute(). Line shifted 283 → 298 by 2026-05-14
    # TIER_2 refactor (receipt-only contract docstring expansion).
    "app/services/gdpr_processor.py:298",
    # gdpr_processor:566 removed 2026-05-04 — Art. 17 erasure refactored
    # to single multi-CTE statement; SQL is built into a `sql` variable
    # then passed to text(sql), so no inline `text(f"…")` site exists.
    # All interpolated values are hardcoded table names from the in-source
    # `all_tables` list (no user input); only `:shop` is bind-bound.
    # report_special_metrics: {bucket} interpolated from `_time_bucket_clause`
    # which returns one of 3 hardcoded `to_char(...)` strings selected by a
    # whitelisted `grain` ∈ {"day", "week", "month"}. Safe.
    "app/services/report_special_metrics.py:165",
    "app/services/report_special_metrics.py:195",
    # alerting.auto_resolve_alerts: {where} interpolated from hardcoded
    # clauses ("source=:source", "alert_type=:alert_type",
    # "shop_domain=:shop_domain", "resolved=false") joined with " AND ".
    # No user input enters the SQL — only the bind values are user-
    # supplied (which ARE bound via params). Safe by construction.
    "app/services/alerting.py:477",
}


def test_no_new_raw_sql_fstring_interpolation():
    """No new `text(f"...")` in app/. The 13 existing sites are
    audited safe and frozen in the allowlist; new ones fail the test
    until they either parameterize or are consciously added with a
    written audit note."""
    hits: list[str] = []
    for file in (_BACKEND / "app").rglob("*.py"):
        if "__pycache__" in file.parts:
            continue
        rel = file.relative_to(_BACKEND).as_posix()
        try:
            source = file.read_text()
        except OSError:
            continue
        for m in _RAW_SQL_FSTRING_PATTERN.finditer(source):
            lineno = source[: m.start()].count("\n") + 1
            key = f"{rel}:{lineno}"
            if key in _RAW_SQL_FSTRING_ALLOWLIST:
                continue
            line = source.splitlines()[lineno - 1].strip()
            hits.append(f"{key}  {line[:90]}")

    assert not hits, (
        f"{len(hits)} new raw-SQL f-string interpolation(s) in app/ — "
        f"parameterize via :bind variables or add to _RAW_SQL_FSTRING_ALLOWLIST "
        f"with a documented audit note:\n  "
        + "\n  ".join(hits[:20])
    )

    # Reverse check: every allowlist entry must still point at an
    # actual `text(f"..."` site. Stale allowlist rows = silent holes
    # where a new unsafe pattern can hide on the same line number.
    # The f-string can be on the same line as `text(` or on the
    # following line (multi-line call), so we scan a small window.
    stale: list[str] = []
    for entry in sorted(_RAW_SQL_FSTRING_ALLOWLIST):
        rel, _, lineno_s = entry.partition(":")
        lineno = int(lineno_s)
        file = _BACKEND / rel
        if not file.exists():
            stale.append(f"{entry} — file missing")
            continue
        lines = file.read_text().splitlines()
        if lineno > len(lines):
            stale.append(f"{entry} — past end of file")
            continue
        window = "\n".join(lines[lineno - 1 : lineno + 3])
        if not _RAW_SQL_FSTRING_PATTERN.search(window):
            stale.append(f"{entry} — no `text(f\"` at this line")
    assert not stale, (
        f"{len(stale)} stale _RAW_SQL_FSTRING_ALLOWLIST entr(ies) — "
        f"refresh line numbers or remove:\n  " + "\n  ".join(stale)
    )


# ---------------------------------------------------------------------------
# 14. Exactly one alembic head — merge-conflict canary
# ---------------------------------------------------------------------------
#
# Multi-head alembic state is the classic "branch merge dropped a
# merge-migration" footgun. Two engineers write migrations against
# the same parent, both merge, and now `alembic upgrade head` is
# ambiguous. Production deploy either picks the wrong branch or
# refuses to run. This test parses the migration files directly
# (no alembic import needed, hermetic) and asserts exactly one
# revision has zero children.
# ---------------------------------------------------------------------------

_REVISION_PATTERN = re.compile(
    r'^revision\s*(?::\s*[^=]+)?=\s*["\']([^"\']+)["\']', re.MULTILINE
)
_DOWN_REVISION_PATTERN = re.compile(
    r'^down_revision\s*(?::\s*[^=]+)?=\s*(.+)$', re.MULTILINE
)


def test_exactly_one_alembic_head():
    """The migrations graph must have exactly one head. Multi-head
    means a prior merge dropped the merge-migration and production
    deploy is now ambiguous."""
    migrations_dir = _BACKEND / "migrations" / "versions"
    assert migrations_dir.exists(), f"no migrations dir at {migrations_dir}"

    revisions: set[str] = set()
    parents: set[str] = set()

    for file in migrations_dir.glob("*.py"):
        if file.name.startswith("_") or file.name == "__init__.py":
            continue
        try:
            source = file.read_text()
        except OSError:
            continue

        rev_match = _REVISION_PATTERN.search(source)
        if not rev_match:
            continue
        revisions.add(rev_match.group(1))

        down_match = _DOWN_REVISION_PATTERN.search(source)
        if not down_match:
            continue
        raw = down_match.group(1).strip().rstrip(",").rstrip()
        if raw in ("None", "none", "null"):
            continue
        # Single parent: "xxx"  |  tuple parent (merge): ("a", "b")
        for token in re.findall(r'["\']([^"\']+)["\']', raw):
            parents.add(token)

    heads = revisions - parents
    assert len(heads) == 1, (
        f"expected exactly 1 alembic head, found {len(heads)}: {sorted(heads)}. "
        f"Multi-head means a merge migration is missing — resolve with "
        f"`alembic merge -m 'merge heads' <head1> <head2>`."
    )


# ---------------------------------------------------------------------------
# 15. No bare print() in prod code paths — logging discipline
# ---------------------------------------------------------------------------
#
# Bare `print(...)` in services/api/workers is a silent data-leak
# surface (structured loggers go to Sentry with PII filters; print
# goes to stdout which uvicorn may capture, rotate, or not — we
# don't know) AND a logging-discipline smell. The rule: prod code
# uses `log = logging.getLogger(__name__)` and calls `log.info(...)`.
# `print` is allowed only in:
#   - scripts/ and app/scripts/ (one-shot CLI utilities)
#   - `regenerate_baselines()` in email_governance.py (dev-only CLI
#     baseline regen, documented as "run after intentional changes")
#   - `if __name__ == "__main__":` guards
# ---------------------------------------------------------------------------

# Allowlist is keyed by (file, enclosing_function_name) — NOT by line
# number. Line numbers shift on every edit above the call site, which
# turned the old line-based allowlist into a treadmill (e.g. the
# 2026-04-22 email_governance expansion shifted regenerate_baselines
# prints 344 → 417 → 389 across three commits). Function names are
# stable across refactors that don't rename the function itself; when
# someone DOES rename a function, the test fails on the renamed call
# site and the rename lands together with the allowlist update —
# which is exactly what we want to review.
_PRINT_ALLOWLIST: set[tuple[str, str]] = {
    # Dev-only CLI baseline regenerator, invoked manually when an
    # email template is intentionally changed. The three prints are
    # the user-copyable new baseline dict.
    ("app/services/email_governance.py", "regenerate_baselines"),
    # Context builder CLI utility — only callers are its own
    # `if __name__ == "__main__":` block and a test. Prints are
    # the user-facing result of a manual doc-sync run.
    ("app/system/context_builder.py", "build_context"),
}


def _build_enclosing_func_map(tree: ast.AST) -> dict[int, str]:
    """Return {lineno: enclosing_function_name} for every line inside a
    `def` or `async def`. Nested functions override outer scopes because
    ast.walk iterates pre-order (outer first, inner last), so later
    assignments win — which is exactly the innermost scope we want.
    Lines outside any function map to nothing (absent key)."""
    by_line: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.lineno
            end = getattr(node, "end_lineno", None) or start
            for ln in range(start, end + 1):
                by_line[ln] = node.name
    return by_line


def test_no_bare_print_in_production_code():
    """`print(...)` in production code bypasses the structured
    logger and leaks to stdout. Use `log.info/warning/error` instead.
    Scans the whole `app/` tree — `app/scripts/` is exempt because
    it holds one-shot CLI utilities that legitimately print to stdout.
    Exceptions are keyed by (file, enclosing-function-name) in
    _PRINT_ALLOWLIST above; `if __name__ == "__main__":` guards are
    auto-exempt."""
    hits: list[str] = []
    for root in [_BACKEND / "app"]:
        if not root.exists():
            continue
        for file in root.rglob("*.py"):
            # app/scripts/ holds one-shot CLI utilities; print is legit there.
            if "scripts" in file.parts:
                continue
            if "__pycache__" in file.parts:
                continue
            rel = file.relative_to(_BACKEND).as_posix()
            try:
                source = file.read_text()
            except OSError:
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue

            enclosing_func = _build_enclosing_func_map(tree)

            # Track which lines are under `if __name__ == "__main__"`
            # — prints there are legit entry-point harness code.
            main_guard_lines: set[int] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.If):
                    test = node.test
                    if (
                        isinstance(test, ast.Compare)
                        and isinstance(test.left, ast.Name)
                        and test.left.id == "__name__"
                        and len(test.comparators) == 1
                        and isinstance(test.comparators[0], ast.Constant)
                        and test.comparators[0].value == "__main__"
                    ):
                        for child in ast.walk(node):
                            if hasattr(child, "lineno"):
                                main_guard_lines.add(child.lineno)

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (isinstance(func, ast.Name) and func.id == "print"):
                    continue
                lineno = node.lineno
                if lineno in main_guard_lines:
                    continue
                func_name = enclosing_func.get(lineno)
                if func_name and (rel, func_name) in _PRINT_ALLOWLIST:
                    continue
                line = source.splitlines()[lineno - 1].strip()
                scope = f"{func_name}()" if func_name else "<module>"
                hits.append(f"{rel}:{lineno} ({scope})  {line[:90]}")

    assert not hits, (
        f"{len(hits)} bare `print(...)` call(s) in production code — "
        f"replace with `log.info/warning/error` or add "
        f"(file, function-name) to _PRINT_ALLOWLIST with justification:\n  "
        + "\n  ".join(hits[:20])
    )


# ---------------------------------------------------------------------------
# 16. Every httpx module-level call has an explicit timeout
# ---------------------------------------------------------------------------
#
# `httpx.get(url)` with no timeout defaults to an INFINITE timeout in
# httpx 0.x/1.x — not the DNS + connect + read budget most developers
# assume. A third-party API hanging will stall a worker thread
# indefinitely, cascading into a pool exhaustion outage. Every
# production system MUST set an explicit timeout on every outbound
# HTTP call. We scan for module-level `httpx.<method>(...)` calls
# (the `httpx.Client` / `httpx.AsyncClient` pattern sets the timeout
# on the client object and is handled separately — those are NOT
# flagged because the flag would be unreachable from the call site
# alone).
#
# Currently: 31 sites, 31 with `timeout=` — zero debt. Pure lock-in.
# ---------------------------------------------------------------------------

_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "request", "head"}


def test_no_httpx_call_without_timeout():
    """Every `httpx.<method>(...)` module-level call in `app/` must
    pass an explicit `timeout=` kwarg. Default is infinite — one
    hung third-party endpoint will stall a worker forever."""
    hits: list[str] = []
    for file in (_BACKEND / "app").rglob("*.py"):
        if "__pycache__" in file.parts:
            continue
        rel = file.relative_to(_BACKEND).as_posix()
        try:
            source = file.read_text()
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if not (isinstance(f, ast.Attribute) and f.attr in _HTTP_METHODS):
                continue
            if not (isinstance(f.value, ast.Name) and f.value.id == "httpx"):
                continue
            kw_names = {kw.arg for kw in node.keywords if kw.arg is not None}
            # `**kwargs` expansion (kw.arg is None) could carry timeout
            # opaquely — accept as a conservative pass.
            if any(kw.arg is None for kw in node.keywords):
                continue
            if "timeout" in kw_names:
                continue
            line = source.splitlines()[node.lineno - 1].strip()
            hits.append(f"{rel}:{node.lineno}  {line[:90]}")

    assert not hits, (
        f"{len(hits)} `httpx` call(s) without explicit `timeout=` in app/. "
        f"An unset timeout is infinite — a hung third-party endpoint will "
        f"stall the worker forever. Add `timeout=<float>` to each site:\n  "
        + "\n  ".join(hits[:20])
    )


# ---------------------------------------------------------------------------
# 17. No wildcard imports in app/
# ---------------------------------------------------------------------------
#
# `from foo import *` imports every public name, silently shadowing
# locals and making refactors dangerous (renaming a foo.x can break
# an unrelated file that relied on the wildcard pull). Also defeats
# static analysis: linters can't track which names came from where.
# Legit use cases are rare (re-export barrel modules, plugin systems)
# and our codebase has none.
#
# Currently: 0 sites. Pure lock-in.
# ---------------------------------------------------------------------------

def test_no_wildcard_imports_in_app():
    """`from x import *` in `app/` is banned. Use explicit names."""
    hits: list[str] = []
    for file in (_BACKEND / "app").rglob("*.py"):
        if "__pycache__" in file.parts:
            continue
        rel = file.relative_to(_BACKEND).as_posix()
        try:
            source = file.read_text()
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            for alias in node.names:
                if alias.name == "*":
                    hits.append(f"{rel}:{node.lineno}  from {node.module} import *")

    assert not hits, (
        f"{len(hits)} wildcard import(s) in app/ — replace with explicit "
        f"names:\n  " + "\n  ".join(hits[:20])
    )


# ---------------------------------------------------------------------------
# 18. No bare `except:` in app/
# ---------------------------------------------------------------------------
#
# `except:` with no exception class catches EVERYTHING including
# `KeyboardInterrupt` (Ctrl-C) and `SystemExit` (sys.exit/uvicorn
# shutdown), which are both CRITICAL to propagate for operator
# control and graceful worker shutdown. Use `except Exception:` to
# catch application errors and let the two exit signals through.
#
# Currently: 0 sites. Pure lock-in.
# ---------------------------------------------------------------------------

def test_no_bare_except_in_app():
    """`except:` (no class) catches KeyboardInterrupt and SystemExit
    too, breaking operator control. Use `except Exception:` instead."""
    hits: list[str] = []
    for file in (_BACKEND / "app").rglob("*.py"):
        if "__pycache__" in file.parts:
            continue
        rel = file.relative_to(_BACKEND).as_posix()
        try:
            source = file.read_text()
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                hits.append(f"{rel}:{node.lineno}")

    assert not hits, (
        f"{len(hits)} bare `except:` clause(s) in app/ — use `except Exception:` "
        f"to let KeyboardInterrupt/SystemExit propagate:\n  "
        + "\n  ".join(hits[:20])
    )


# ---------------------------------------------------------------------------
# 19. No `os.environ["X"]` subscript without a default
# ---------------------------------------------------------------------------
#
# `os.environ["X"]` raises `KeyError` at runtime when X is missing.
# On a worker import path or a startup hook this crashes the whole
# process with a naked stack trace that gives no hint which env var
# is missing. Use `os.getenv("X")` (returns None) or
# `os.getenv("X", "default")` — both fail loudly with a clear name.
#
# Currently: 0 sites. Pure lock-in.
# ---------------------------------------------------------------------------

def test_no_os_environ_direct_subscript():
    """`os.environ["X"]` is banned — use `os.getenv("X")` so missing
    env vars raise with a clear name instead of crashing import."""
    hits: list[str] = []
    for file in (_BACKEND / "app").rglob("*.py"):
        if "__pycache__" in file.parts:
            continue
        rel = file.relative_to(_BACKEND).as_posix()
        try:
            source = file.read_text()
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Subscript):
                continue
            v = node.value
            if not (isinstance(v, ast.Attribute) and v.attr == "environ"):
                continue
            if not (isinstance(v.value, ast.Name) and v.value.id == "os"):
                continue
            hits.append(f"{rel}:{node.lineno}")

    assert not hits, (
        f"{len(hits)} `os.environ[...]` subscript(s) in app/ — use "
        f"`os.getenv(...)` for graceful None fallback and clearer error "
        f"messages:\n  " + "\n  ".join(hits[:20])
    )


# ---------------------------------------------------------------------------
# 20. No f-string interpolation in logger calls
# ---------------------------------------------------------------------------
#
# `log.info(f"user {user.id} did X")` evaluates the f-string ALWAYS,
# even when the log level filters the message out. Use positional:
# `log.info("user %s did X", user.id)` — the % formatting is lazy,
# only evaluated if the handler actually emits the record. At 10k
# merchants × multiple workers this is a measurable CPU win AND
# prevents accidental PII leakage into f-string buffers that might
# show up in tracebacks later.
#
# Currently: 0 sites. Pure lock-in.
# ---------------------------------------------------------------------------

_LOG_LEVELS = {"debug", "info", "warning", "error", "critical", "exception"}


def test_no_fstring_in_log_calls():
    """`log.info(f"...")` evaluates the f-string eagerly even when
    filtered out. Use positional `log.info("...", arg)` instead."""
    hits: list[str] = []
    for file in (_BACKEND / "app").rglob("*.py"):
        if "__pycache__" in file.parts:
            continue
        rel = file.relative_to(_BACKEND).as_posix()
        try:
            source = file.read_text()
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if not isinstance(f, ast.Attribute):
                continue
            if f.attr not in _LOG_LEVELS:
                continue
            recv = f.value
            if not isinstance(recv, ast.Name):
                continue
            # Only flag receivers that look like loggers — `log`,
            # `logger`, or anything ending in `_log` / `_logger`.
            name_l = recv.id.lower()
            if name_l not in {"log", "logger"} and not (
                name_l.endswith("_log") or name_l.endswith("_logger")
            ):
                continue
            if not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.JoinedStr):
                hits.append(f"{rel}:{node.lineno}")

    assert not hits, (
        f"{len(hits)} f-string logger call(s) in app/ — use positional "
        f"formatting `log.info('msg %s', arg)` for lazy evaluation:\n  "
        + "\n  ".join(hits[:20])
    )


# ---------------------------------------------------------------------------
# 21. No orphan APIRouter files in app/api/
# ---------------------------------------------------------------------------
#
# Every file in `app/api/` that defines `router = APIRouter(...)` MUST
# be imported in `app/main.py`. Orphan router files are dead code that
# ship in the bundle, pollute grep results, and — worst — can be
# imported accidentally later and register routes nobody reviewed.
# CLAUDE.md §2 rule 7 bans this pattern explicitly.
#
# Currently: 0 sites. The `revenue_actions.py` orphan shipped with
# this batch was deleted in the same commit.
# ---------------------------------------------------------------------------

def test_no_orphan_api_routers():
    """Every `router = APIRouter(...)` file under `app/api/` must
    be imported by `app/main.py`."""
    api_dir = _BACKEND / "app" / "api"
    main_src = (_BACKEND / "app" / "main.py").read_text()

    orphans: list[str] = []
    for file in api_dir.rglob("*.py"):
        if file.name == "__init__.py" or "__pycache__" in file.parts:
            continue
        try:
            source = file.read_text()
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue

        has_router = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
                continue
            if node.targets[0].id != "router":
                continue
            if not isinstance(node.value, ast.Call):
                continue
            f = node.value.func
            if (isinstance(f, ast.Name) and f.id == "APIRouter") or (
                isinstance(f, ast.Attribute) and f.attr == "APIRouter"
            ):
                has_router = True
                break
        if not has_router:
            continue

        stem = file.stem
        if f"from app.api.{stem}" not in main_src and f"from .api.{stem}" not in main_src:
            orphans.append(str(file.relative_to(_BACKEND)))

    assert not orphans, (
        f"{len(orphans)} orphan router file(s) in app/api/ — either wire "
        f"them into app/main.py or delete them:\n  "
        + "\n  ".join(orphans)
    )


# ---------------------------------------------------------------------------
# 22. No `requests` library usage — httpx-only
# ---------------------------------------------------------------------------
#
# The backend standardizes on `httpx` for HTTP calls (supports async,
# has a Timeout class, Client pooling, retries via transports). The
# `requests` library is a legacy alternative that doesn't speak async
# and has its own quirks — mixing both is a code-smell and makes
# retries/timeouts inconsistent. Currently zero usage; this is
# pure lock-in to prevent drift.
# ---------------------------------------------------------------------------

def test_no_requests_library_usage():
    """No `import requests` / `from requests import ...` in app/.
    The backend uses `httpx` exclusively."""
    hits: list[str] = []
    for file in (_BACKEND / "app").rglob("*.py"):
        if "__pycache__" in file.parts:
            continue
        rel = file.relative_to(_BACKEND).as_posix()
        try:
            source = file.read_text()
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "requests" or alias.name.startswith("requests."):
                        hits.append(f"{rel}:{node.lineno}  import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module == "requests" or (
                    node.module and node.module.startswith("requests.")
                ):
                    hits.append(f"{rel}:{node.lineno}  from {node.module} import ...")

    assert not hits, (
        f"{len(hits)} `requests` library usage(s) in app/ — use `httpx` "
        f"instead (async-capable, pooled, standardized):\n  "
        + "\n  ".join(hits[:20])
    )


# ---------------------------------------------------------------------------
# 23. Tracker JS source hash matches TRACKER_VERSION pair
# ---------------------------------------------------------------------------
#
# `TRACKER_VERSION` in `app/core/tracker_version.py` is appended as
# `?v={version}` to every merchant's spark-tracker.js script tag URL.
# When a tracker file is edited, TRACKER_VERSION MUST be bumped so
# merchant browsers cache-bust and fetch the new version on next load.
# Forgetting to bump it means merchants keep serving stale JS —
# potentially for weeks — until the onboarding repair cycle notices
# the URL drift.
#
# This test pairs a content hash with the version: any change to
# `/opt/wishspark/tracker/*.js` changes the hash, the test fails,
# and the developer MUST bump TRACKER_VERSION + update the hash in
# the same commit. No way to silently ship a tracker change.
# ---------------------------------------------------------------------------

def test_tracker_js_hash_matches_version():
    """The declared TRACKER_SOURCE_HASH in tracker_version.py must
    equal the actual SHA-256 of tracker/*.js. If a tracker script
    changed without a matching TRACKER_VERSION bump + hash update,
    merchants will serve cached stale JS."""
    import hashlib

    from app.core.tracker_version import TRACKER_SOURCE_HASH, TRACKER_VERSION

    tracker_dir = _TRACKER
    assert tracker_dir.exists(), f"tracker dir missing at {tracker_dir}"

    files = sorted(p for p in tracker_dir.glob("*.js") if p.is_file())
    assert files, f"no tracker/*.js files found in {tracker_dir}"

    h = hashlib.sha256()
    for f in files:
        h.update(f.name.encode())
        h.update(b"\0")
        h.update(f.read_bytes())
        h.update(b"\0")
    actual = h.hexdigest()

    assert actual == TRACKER_SOURCE_HASH, (
        f"tracker/*.js changed but TRACKER_VERSION and TRACKER_SOURCE_HASH "
        f"in app/core/tracker_version.py were not updated.\n"
        f"  current version   : {TRACKER_VERSION}\n"
        f"  declared hash     : {TRACKER_SOURCE_HASH}\n"
        f"  actual hash       : {actual}\n"
        f"Fix: bump TRACKER_VERSION (merchant cache-bust) AND paste the "
        f"`actual` hash above as the new TRACKER_SOURCE_HASH in the same "
        f"commit. Files hashed: {[f.name for f in files]}"
    )


# ---------------------------------------------------------------------------
# 24. No orphan service modules
# ---------------------------------------------------------------------------
#
# Every file in `app/services/` must be imported by at least one
# other `app/` or `tests/` module. Orphan service files are usually
# speculative V1/V3 code that never got wired end-to-end — or were
# wired and the consumer was deleted, leaving the service behind.
# Both are pure ballast: they pollute grep results, confuse new
# contributors, and risk being imported accidentally by an LLM
# patch that finds the name in the symbol table.
#
# Currently: 0 orphans. Two shipped before this test (`attribution.py`,
# `intent_engine.py`) were deleted in the same commit. Service count
# is 185.
# ---------------------------------------------------------------------------

def test_no_orphan_service_modules():
    """Every `app/services/*.py` module must be imported somewhere in
    `app/` or `tests/`. Orphan services are dead weight."""
    services_dir = _BACKEND / "app" / "services"
    service_stems: dict[str, str] = {}
    for file in services_dir.rglob("*.py"):
        if file.name == "__init__.py" or "__pycache__" in file.parts:
            continue
        rel = file.relative_to(_BACKEND).as_posix()
        service_stems[file.stem] = rel

    imported: set[str] = set()
    for root in [_BACKEND / "app", _BACKEND / "tests"]:
        for file in root.rglob("*.py"):
            if "__pycache__" in file.parts:
                continue
            try:
                tree = ast.parse(file.read_text())
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.startswith("app.services."):
                        imported.add(node.module.split(".")[-1])
                    elif node.module == "app.services":
                        # `from app.services import svc_name` — imported
                        # name IS the service module stem.
                        for alias in node.names:
                            imported.add(alias.name)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("app.services."):
                            imported.add(alias.name.split(".")[-1])

    orphans = sorted(set(service_stems.keys()) - imported)
    assert not orphans, (
        f"{len(orphans)} orphan service module(s) in app/services/ — "
        f"either wire them into a caller or delete per CLAUDE.md §2 rule 7:\n  "
        + "\n  ".join(service_stems[o] for o in orphans)
    )


# ---------------------------------------------------------------------------
# 25. Every worker file is registered in ecosystem.config.js
# ---------------------------------------------------------------------------
#
# PM2 spawns worker processes from `ecosystem.config.js`. Every
# `app/workers/*_worker.py` file MUST be referenced there — otherwise
# it's orphan code that silently fails to run in production.
# Conversely, every `script`/`args` in ecosystem that points at a
# `app/workers/` path must point at a file that actually exists.
# ---------------------------------------------------------------------------

def test_every_worker_file_registered_in_ecosystem():
    """Each `app/workers/*_worker.py` file must be referenced in
    `/opt/wishspark/ecosystem.config.js`, and vice versa — no
    dangling references on either side."""
    ecosystem_path = _REPO_ROOT / "ecosystem.config.js"
    assert ecosystem_path.exists(), "ecosystem.config.js missing"
    ecosystem_src = ecosystem_path.read_text()

    worker_files: set[str] = set()
    for file in (_BACKEND / "app" / "workers").glob("*_worker.py"):
        worker_files.add(file.name)

    # Every worker file must appear somewhere in the ecosystem file
    unregistered: list[str] = []
    for name in sorted(worker_files):
        if name not in ecosystem_src:
            unregistered.append(name)
    assert not unregistered, (
        f"{len(unregistered)} worker file(s) not referenced in ecosystem.config.js — "
        f"PM2 won't spawn them, they're silent dead code:\n  "
        + "\n  ".join(unregistered)
    )

    # Every ecosystem reference to `app/workers/*_worker.py` must
    # point at a file that exists.
    dangling: list[str] = []
    for m in re.finditer(r'app/workers/(\w+_worker)\.py', ecosystem_src):
        fname = m.group(1) + ".py"
        if fname not in worker_files:
            dangling.append(fname)
    assert not dangling, (
        f"{len(dangling)} dangling worker reference(s) in ecosystem.config.js — "
        f"PM2 config points at files that don't exist:\n  "
        + "\n  ".join(dangling)
    )


# ---------------------------------------------------------------------------
# 26. No backup/scratch files in the source tree
# ---------------------------------------------------------------------------
#
# `.backup`, `.bak`, `.old`, `*-backup*`, `*.LITE-STABLE`, `*.save`
# files are git-history-done-wrong. Git IS the backup system: every
# prior state of every file is recoverable via `git log -- <path>`
# and `git show <sha>:<path>`. Named backup files rot, mislead grep,
# pollute IDE indexing, bloat the repo, and multiply silently — one
# 4758-line `page.tsx.backup` triggers a reflex to add more. The
# only acceptable way to save a working-in-progress state is either
# (a) commit it to a branch or (b) stash it. Never a named copy.
#
# Currently: 0 sites. 38 backup files were deleted in the same
# commit that shipped this test (32 tracked, 6 untracked, total
# 1.5 MB of dead weight across dashboard/src/app/ and
# backend/app/core/).
# ---------------------------------------------------------------------------

_BACKUP_PATTERN = re.compile(
    r"\.(backup|bak|old|save|orig)$|-backup|LITE-STABLE|before-[a-z-]+$",
    re.IGNORECASE,
)


def test_no_backup_files_in_source_tree():
    """No named backup files. Git is the backup. If you need a WIP
    checkpoint, commit a branch or stash — never copy the file with
    a `-backup` suffix."""
    hits: list[str] = []
    roots = [
        _DASHBOARD / "src",
        _BACKEND / "app",
        _BACKEND / "tests",
        _BACKEND / "scripts",
        _TRACKER,
    ]
    for root in roots:
        if not root.exists():
            continue
        for file in root.rglob("*"):
            if not file.is_file():
                continue
            if any(p in file.parts for p in ("node_modules", ".next", "__pycache__", "venv")):
                continue
            if _BACKUP_PATTERN.search(file.name):
                try:
                    rel = file.relative_to(_REPO_ROOT).as_posix()
                except ValueError:
                    rel = str(file)
                hits.append(rel)

    assert not hits, (
        f"{len(hits)} backup/scratch file(s) in source tree — delete them "
        f"and use git commits/stashes for WIP state:\n  "
        + "\n  ".join(hits[:30])
    )


# ---------------------------------------------------------------------------
# 27. No legacy `.query(Model).get(id)` calls — SQLAlchemy 2.0
# ---------------------------------------------------------------------------
#
# `Query.get()` is deprecated in SQLAlchemy 2.0 and emits
# `LegacyAPIWarning` on every call. The modern replacement is
# `Session.get(Model, id)` — semantically identical, supported
# long-term. The 2026-04-15 sweep migrated 74 sites across 19 files.
# This test freezes the sweep: any new `.query(X).get(id)` fails
# with a clear fix pointer.
#
# Uses AST (Attribute chain walking) to be robust against
# whitespace, line breaks, and identifier variations.
# ---------------------------------------------------------------------------

def test_no_legacy_query_get_calls():
    """`db.query(Model).get(id)` is deprecated in SQLAlchemy 2.0.
    Use `db.get(Model, id)` instead — same return type, no warning."""
    hits: list[str] = []
    scan_dirs = [_BACKEND / "app", _BACKEND / "tests"]
    for scan_dir in scan_dirs:
        for file in scan_dir.rglob("*.py"):
            if "__pycache__" in file.parts:
                continue
            rel = file.relative_to(_BACKEND).as_posix()
            # Skip this very test file (it references the pattern in comments/docstrings)
            if rel == "tests/test_elite_hardening_v2.py":
                continue
            try:
                source = file.read_text()
                tree = ast.parse(source)
            except (OSError, SyntaxError):
                continue

            for node in ast.walk(tree):
                # Looking for: <something>.query(<Model>).get(<expr>)
                # That's: Call(func=Attribute(attr='get', value=Call(func=Attribute(attr='query', ...))))
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                if not (isinstance(f, ast.Attribute) and f.attr == "get"):
                    continue
                inner = f.value
                if not isinstance(inner, ast.Call):
                    continue
                inner_f = inner.func
                if not (isinstance(inner_f, ast.Attribute) and inner_f.attr == "query"):
                    continue
                # query() must have exactly one positional arg that is a Name
                # starting with uppercase (a Model class, not a Column).
                if len(inner.args) != 1 or inner.keywords:
                    continue
                q_arg = inner.args[0]
                if not (isinstance(q_arg, ast.Name) and q_arg.id[:1].isupper()):
                    continue
                line = source.splitlines()[node.lineno - 1].strip()
                hits.append(f"{rel}:{node.lineno}  {line[:90]}")

    assert not hits, (
        f"{len(hits)} legacy `.query(Model).get(id)` call(s) in app/ + tests/ — "
        f"replace with `db.get(Model, id)` (SQLAlchemy 2.0 modern form, "
        f"semantically identical, no deprecation warning):\n  "
        + "\n  ".join(hits[:20])
    )


# ---------------------------------------------------------------------------
# 28. No FastAPI `@app.on_event` decorators — lifespan required
# ---------------------------------------------------------------------------
#
# FastAPI deprecated `@app.on_event("startup")` / `@app.on_event("shutdown")`
# in favor of the `lifespan` async context manager. The 2026-04-15
# migration moved both `_startup_env_audit` and `_startup_telegram_warmup`
# into `lifespan()` at the top of `app/main.py`. This test freezes the
# migration so a regression cannot land silently.
#
# The regex also catches the router-level form `@router.on_event(...)`
# which is subject to the same deprecation.
# ---------------------------------------------------------------------------

def test_no_fastapi_on_event_decorators():
    """FastAPI `@app.on_event(...)` / `@router.on_event(...)` decorators
    are deprecated. Use the `lifespan` async context manager on the
    FastAPI instance instead. Uses AST to walk real decorator nodes —
    no false positives on docstring mentions."""
    hits: list[str] = []
    for file in (_BACKEND / "app").rglob("*.py"):
        if "__pycache__" in file.parts:
            continue
        rel = file.relative_to(_BACKEND).as_posix()
        try:
            source = file.read_text()
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                # Match @<x>.on_event(...) — deco is ast.Call whose
                # .func is ast.Attribute(attr='on_event').
                if not isinstance(deco, ast.Call):
                    continue
                df = deco.func
                if isinstance(df, ast.Attribute) and df.attr == "on_event":
                    hits.append(f"{rel}:{deco.lineno}  @…on_event(…) → {node.name}")

    assert not hits, (
        f"{len(hits)} `@*.on_event(...)` decorator(s) in app/ — migrate to "
        f"the FastAPI `lifespan` async context manager (see app/main.py for "
        f"the reference pattern):\n  "
        + "\n  ".join(hits[:20])
    )


# ---------------------------------------------------------------------------
# 29. No bare `open()` without context manager in production code
# ---------------------------------------------------------------------------
#
# Bare `open(path)` without `with` leaks file descriptors in long-running
# workers. A leaked FD per worker cycle exhausts OS ulimits within hours.
# The 2026-04-16 hunt found one real leak in bugfix_prompt_grounding.py.
# This test prevents new occurrences.
#
# Detection: AST walk looking for ast.Call where func is ast.Name(id='open')
# that is NOT the context_expr of a `with` statement.
# ---------------------------------------------------------------------------

def test_no_bare_open_without_context_manager():
    """Every `open()` in production code must be inside a `with` block."""
    hits: list[str] = []
    for file in (_BACKEND / "app").rglob("*.py"):
        if "__pycache__" in file.parts:
            continue
        rel = file.relative_to(_BACKEND).as_posix()
        try:
            source = file.read_text()
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue

        # Collect all open() calls that ARE context exprs in `with` stmts
        with_opens: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    ce = item.context_expr
                    if isinstance(ce, ast.Call) and isinstance(ce.func, ast.Name) and ce.func.id == "open":
                        with_opens.add(id(ce))

        # Now find all open() calls NOT in that set
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Name) and node.func.id == "open"):
                continue
            if id(node) in with_opens:
                continue
            line = source.splitlines()[node.lineno - 1].strip()
            hits.append(f"{rel}:{node.lineno}  {line[:90]}")

    assert not hits, (
        f"{len(hits)} bare `open()` call(s) without `with` context manager in app/ — "
        f"file descriptors leak in long-running workers. Wrap in `with open(...) as fh:`\n  "
        + "\n  ".join(hits[:20])
    )


# ---------------------------------------------------------------------------
# 30. No `except Exception: pass` in production code (without logging)
# ---------------------------------------------------------------------------
#
# `except Exception: pass` silently hides production failures. The
# 2026-04-16 audit found 5 critical silent sinks. Every except block
# must have at least a `log.` call or `record_silent_return` invocation.
# This test prevents new silent sinks from landing.
#
# Allowlist: none needed so far. If a legitimate case arises, add it
# with an inline `# SILENT-EXCEPT-OK: <reason>` marker.
# ---------------------------------------------------------------------------

def test_no_silent_except_pass():
    """Silent `except Exception: pass` count must not grow.

    Baseline: 394 (measured 2026-04-16). As sinks are fixed, ratchet the
    ceiling down. To exempt a legitimate case, add `# SILENT-EXCEPT-OK: <reason>`
    on the `pass` line."""
    _CEILING = 0  # 394 → 0 on 2026-04-16. Zero silent sinks in the entire codebase.
    hits: list[str] = []
    for file in (_BACKEND / "app").rglob("*.py"):
        if "__pycache__" in file.parts:
            continue
        rel = file.relative_to(_BACKEND).as_posix()
        try:
            source = file.read_text()
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        lines = source.splitlines()

        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if node.type is None:
                pass
            elif isinstance(node.type, ast.Name) and node.type.id == "Exception":
                pass
            else:
                continue

            body_stmts = [s for s in node.body if not isinstance(s, ast.Expr) or not isinstance(s.value, ast.Constant)]
            if len(body_stmts) == 1 and isinstance(body_stmts[0], ast.Pass):
                pass_line = lines[body_stmts[0].lineno - 1] if body_stmts[0].lineno <= len(lines) else ""
                if "SILENT-EXCEPT-OK:" in pass_line:
                    continue
                line = lines[node.lineno - 1].strip()
                hits.append(f"{rel}:{node.lineno}  {line[:90]}")

    assert len(hits) <= _CEILING, (
        f"Silent `except Exception: pass` count grew to {len(hits)} (ceiling: {_CEILING}). "
        f"New sinks MUST log. Fix or mark `# SILENT-EXCEPT-OK: <reason>`:\n  "
        + "\n  ".join(hits[:20])
    )


# ---------------------------------------------------------------------------
# 31. No Redis GET-then-DELETE without atomicity (TOCTOU race)
# ---------------------------------------------------------------------------
#
# Pattern: `val = rc.get(key); if val: rc.delete(key)` is a TOCTOU race
# when used for consume-once tokens (confirmations, OTPs, dedup keys).
# Use `rc.getdel(key)` (Redis 6.2+) or a Lua script instead.
# The 2026-04-16 audit found one real race in telegram_safety.py.
#
# Detection: simple source-text scan for `rc.get(` followed by `rc.delete(`
# within 5 lines. AST would be more precise but this catches the pattern.
# ---------------------------------------------------------------------------

def test_no_redis_get_then_delete_race():
    """Redis consume-once tokens must use atomic GETDEL, not GET+DELETE."""
    hits: list[str] = []
    for file in (_BACKEND / "app").rglob("*.py"):
        if "__pycache__" in file.parts:
            continue
        rel = file.relative_to(_BACKEND).as_posix()
        try:
            lines = file.read_text().splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines):
            if "rc.get(" not in line and ".get(" not in line:
                continue
            # Look ahead 5 lines for a .delete( on the same key
            window = "\n".join(lines[i:i + 6])
            if "rc.delete(" in window or ".delete(" in window:
                # Check it's a GET-then-DELETE pattern (not just unrelated calls)
                stripped = line.strip()
                if "= rc.get(" in stripped or "= _client().get(" in stripped:
                    # GETDEL-OK marker for intentional patterns
                    if "GETDEL-OK:" in window:
                        continue
                    hits.append(f"{rel}:{i + 1}  {stripped[:90]}")

    assert not hits, (
        f"{len(hits)} Redis GET-then-DELETE TOCTOU race(s) in app/ — "
        f"use `rc.getdel(key)` (Redis 6.2+) for consume-once tokens. "
        f"If intentional (e.g. read-only check), add `# GETDEL-OK: <reason>` nearby:\n  "
        + "\n  ".join(hits[:20])
    )


# ---------------------------------------------------------------------------
# 32. Every /pro/* and /merchant/* route requires authentication
# ---------------------------------------------------------------------------
#
# The 2026-04-16 audit found /pro/ads/networks was unprotected. This test
# ensures every /pro/ and /merchant/ route has a require_* dependency.
# Uses source text scanning (not AST) because FastAPI decorators are
# single-line strings and auth deps are always in the function signature.
# ---------------------------------------------------------------------------

def test_every_pro_merchant_route_requires_auth():
    """All /pro/ and /merchant/ routes must require authentication."""
    import re
    hits: list[str] = []
    _AUTH_DEPS = ("require_pro_session", "require_scale_session", "require_merchant_session", "require_ops_key", "require_operator")

    for file in (_BACKEND / "app" / "api").rglob("*.py"):
        if "__pycache__" in file.parts:
            continue
        rel = file.relative_to(_BACKEND).as_posix()
        lines = file.read_text().splitlines()

        for i, line in enumerate(lines):
            # Match route decorators containing /pro/ or /merchant/
            if not re.search(r'@router\.(get|post|put|patch|delete|options)\(.*"/(?:pro|merchant)/', line):
                continue
            # Look ahead 16 lines for an auth dependency
            window = "\n".join(lines[i:i + 16])
            if not any(dep in window for dep in _AUTH_DEPS):
                hits.append(f"{rel}:{i + 1}  {line.strip()[:90]}")

    assert not hits, (
        f"{len(hits)} /pro/ or /merchant/ route(s) without authentication:\n  "
        + "\n  ".join(hits[:20])
        + "\n\nEvery /pro/* route needs `Depends(require_pro_session)` and "
        f"every /merchant/* route needs `Depends(require_merchant_session)`."
    )


# ---------------------------------------------------------------------------
# 33. Every /track and /track/batch payload field has Pydantic bounds
# ---------------------------------------------------------------------------
#
# The track endpoint is the widest unauthenticated surface. Every numeric
# field must have ge=/le= bounds to prevent DB overflow on commit.
# The 2026-04-16 audit found timestamp, dwell_seconds, scroll_depth
# were unbounded — fixed in the same session.
# ---------------------------------------------------------------------------

def test_track_payload_fields_have_bounds():
    """TrackPayload numeric fields must have ge=/le= bounds."""
    import importlib
    track_mod = importlib.import_module("app.api.track")
    TrackPayload = track_mod.TrackPayload
    schema = TrackPayload.model_json_schema()
    props = schema.get("properties", {})

    numeric_fields = ["timestamp", "dwell_seconds", "scroll_depth"]
    unbounded: list[str] = []
    for field_name in numeric_fields:
        if field_name not in props:
            continue
        spec = props[field_name]
        # Check for anyOf (Optional) pattern
        if "anyOf" in spec:
            for variant in spec["anyOf"]:
                if variant.get("type") == "integer":
                    spec = variant
                    break
        has_ge = "minimum" in spec or "exclusiveMinimum" in spec
        has_le = "maximum" in spec or "exclusiveMaximum" in spec
        if not (has_ge and has_le):
            unbounded.append(f"{field_name}: min={has_ge}, max={has_le}")

    assert not unbounded, (
        f"TrackPayload numeric fields without bounds (DB overflow risk):\n  "
        + "\n  ".join(unbounded)
    )


# ---------------------------------------------------------------------------
# 34. No ::float casts on money/revenue NUMERIC columns in SQL
# ---------------------------------------------------------------------------
#
# The 2026-04-16 audit found 12 SQL queries casting NUMERIC(18,2) money
# columns to ::float, introducing IEEE 754 precision loss on aggregations.
# This test prevents new ::float casts on money-related column names.
# ---------------------------------------------------------------------------

def test_no_float_cast_on_money_columns():
    """SQL queries must not cast money NUMERIC columns to ::float."""
    import re
    _MONEY_PATTERNS = re.compile(
        r"::float\b.*\b(total_price|revenue|aov|gross_revenue|covered_revenue|amount_eur|cost_eur|refund)"
        r"|\b(total_price|revenue|aov|gross_revenue|covered_revenue|amount_eur|cost_eur|refund)\b.*::float"
    )
    hits: list[str] = []
    scan_dirs = [_BACKEND / "app"]
    for scan_dir in scan_dirs:
        for file in scan_dir.rglob("*.py"):
            if "__pycache__" in file.parts:
                continue
            rel = file.relative_to(_BACKEND).as_posix()
            try:
                lines = file.read_text().splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines):
                if _MONEY_PATTERNS.search(line):
                    hits.append(f"{rel}:{i + 1}  {line.strip()[:90]}")

    assert not hits, (
        f"{len(hits)} SQL ::float cast(s) on money NUMERIC columns — "
        f"precision loss on large aggregations. Remove ::float, let Python "
        f"Decimal handle the value:\n  "
        + "\n  ".join(hits[:20])
    )
