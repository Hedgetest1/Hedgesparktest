#!/usr/bin/env python3
"""suggest_test_exempts.py — one-shot detector for endpoints that can
auto-receive a `# test-exempt: <reason>` tag based on structural pattern
signals.

Triggers (high-confidence only — we'd rather miss than mis-tag):

  sse-stream        handler returns StreamingResponse / EventSourceResponse
                    OR body contains `yield f"event: ...\\ndata: ..."`
                    (the canonical Server-Sent Events emission pattern)

  webhook-receiver  file in app/api/ whose filename contains "webhook" +
                    handler body references one of the known HMAC
                    signature headers (X-Hub-Signature, X-Shopify-Hmac,
                    X-Sentry-Signature, svix-signature, resend-signature,
                    X-Telegram-Bot-Api-Secret-Token) OR `_verify_hmac`

  oauth-callback    route path ends with `/oauth/callback` OR ends in
                    `/callback` AND method is GET AND handler returns
                    RedirectResponse / HTMLResponse

  deprecated        route decorator has `deprecated=True` OR first
                    docstring line starts with "Deprecated" /
                    "DEPRECATED" / "[DEPRECATED]"

Usage
-----
    ./suggest_test_exempts.py            # print proposals, don't modify
    ./suggest_test_exempts.py --apply    # insert tags into source files

Any proposal is conservative: all category-triggers require a match on
the CANONICAL signal, not a heuristic. Misses are fine; mis-tags would
silence real test gaps.
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys
from collections import defaultdict
from dataclasses import dataclass

BACKEND_ROOT = pathlib.Path("/opt/wishspark/backend")
BACKEND_API = BACKEND_ROOT / "app" / "api"

_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}

# Webhook signature / verification signals. The handler body rarely
# embeds the raw header string — it delegates to a verifier helper.
# Cover both patterns: literal header names AND common verifier names.
_WEBHOOK_HEADER_SIGNALS = (
    # Literal header names (handler body may reference directly)
    "x-hub-signature",
    "x-shopify-hmac",
    "x-sentry-signature",
    "svix-signature",
    "resend-signature",
    "x-telegram-bot-api-secret-token",
    # Verifier function call names (module-local helpers)
    "_verify_hmac",
    "_verify_webhook",
    "_verify_signature",
    "verify_webhook_signature",
    "verify_shopify_hmac",
    "verify_resend_signature",
    "verify_sentry_signature",
    "verify_telegram_signature",
)

# SSE emission pattern — handler yields `event: X\ndata: Y` chunks.
_SSE_EMISSION_RE = re.compile(r"yield\s+[^;]*event:\s*[a-z]")
# OR returns StreamingResponse.
_STREAMING_RESPONSE_NAMES = ("StreamingResponse", "EventSourceResponse")

# Docstring deprecation markers.
_DEPRECATED_DOC_RE = re.compile(
    r"^\s*(?:\[DEPRECATED\]|DEPRECATED\b|Deprecated\b)", re.MULTILINE
)


@dataclass
class Proposal:
    file: str
    decorator_line: int
    method: str
    path: str
    reason: str
    signals: list[str]


def _parse_router_prefixes(tree: ast.Module) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not (isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "APIRouter"):
            continue
        prefix = ""
        for kw in node.value.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                prefix = kw.value.value.rstrip("/")
                break
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = prefix
    return out


def _decorator_info(dec: ast.expr) -> dict | None:
    if not isinstance(dec, ast.Call):
        return None
    fn = dec.func
    if not isinstance(fn, ast.Attribute):
        return None
    if not isinstance(fn.value, ast.Name):
        return None
    if fn.attr not in _HTTP_METHODS:
        return None
    if not dec.args:
        return None
    first = dec.args[0]
    if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
        return None
    deprecated = False
    for kw in dec.keywords:
        if kw.arg == "deprecated":
            if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                deprecated = True
    return {
        "router_var": fn.value.id,
        "method": fn.attr.upper(),
        "path": first.value,
        "deprecated_kw": deprecated,
    }


def _has_existing_tag(text_lines: list[str], dec: ast.expr) -> bool:
    """True if ANY test-exempt or ui-exempt tag already present on
    decorator's source span. Never re-propose where a tag exists."""
    start = dec.lineno
    end = getattr(dec, "end_lineno", dec.lineno) or dec.lineno
    for ln in range(start, end + 1):
        idx = ln - 1
        if 0 <= idx < len(text_lines):
            if "test-exempt:" in text_lines[idx]:
                return True
    return False


def _returns_streaming_response(handler: ast.AST) -> tuple[bool, list[str]]:
    """Detect SSE stream pattern."""
    signals: list[str] = []
    for node in ast.walk(handler):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _STREAMING_RESPONSE_NAMES:
                signals.append(f"calls {node.func.id}()")
            elif isinstance(node.func, ast.Attribute) and node.func.attr in _STREAMING_RESPONSE_NAMES:
                signals.append(f"returns {node.func.attr}()")
    src = ast.unparse(handler)
    if _SSE_EMISSION_RE.search(src):
        signals.append("yields `event: ...\\ndata: ...` pattern")
    return (bool(signals), signals)


def _references_webhook_header(handler: ast.AST, file_name: str) -> tuple[bool, list[str]]:
    """Detect webhook-receiver pattern."""
    signals: list[str] = []
    if "webhook" in file_name.lower():
        signals.append(f"file name `{file_name}`")
    src = ast.unparse(handler).lower()
    for sig in _WEBHOOK_HEADER_SIGNALS:
        if sig in src:
            signals.append(f"references `{sig}`")
    return (len(signals) >= 2, signals)


def _is_oauth_callback(method: str, path: str, handler: ast.AST) -> tuple[bool, list[str]]:
    """Detect OAuth redirect-landing pattern. The structural signal is
    canonical: method=GET + path ending in `/oauth/callback` or
    `/callback` in an oauth-context path. Response type check is
    secondary — many handlers delegate to a helper that returns
    HTMLResponse/RedirectResponse, so the AST scan of the handler body
    alone may miss the literal class name."""
    signals: list[str] = []
    if method != "GET":
        return (False, [])
    if path.endswith("/oauth/callback"):
        signals.append("path ends with `/oauth/callback`")
    elif path.endswith("/callback") and "oauth" in path:
        signals.append(f"path `{path}` in oauth context")
    else:
        return (False, [])
    # Strengthen with response-type evidence IF available. Missing is
    # fine — the path+method combo is already canonical.
    src = ast.unparse(handler)
    if "RedirectResponse" in src:
        signals.append("returns RedirectResponse")
    elif "HTMLResponse" in src:
        signals.append("returns HTMLResponse")
    return (True, signals)


def _is_deprecated(dec_info: dict, handler: ast.AST) -> tuple[bool, list[str]]:
    signals: list[str] = []
    if dec_info["deprecated_kw"]:
        signals.append("decorator has deprecated=True")
    doc = ast.get_docstring(handler)
    if doc and _DEPRECATED_DOC_RE.search(doc):
        signals.append("docstring starts with DEPRECATED")
    return (bool(signals), signals)


def _classify_handler(
    dec_info: dict, dec_node: ast.expr, handler: ast.AST, file_name: str
) -> tuple[str, list[str]] | None:
    """Return (reason, signals) for the highest-confidence category match,
    else None. Check order matters — deprecated wins over pattern
    signals (a deprecated SSE still only needs the deprecated tag)."""
    # deprecated first — kills any further consideration
    ok, sigs = _is_deprecated(dec_info, handler)
    if ok:
        return ("deprecated", sigs)
    # oauth-callback — only applies to GET methods
    ok, sigs = _is_oauth_callback(dec_info["method"], _dec_full_path(dec_info),
                                  handler)
    if ok:
        return ("oauth-callback", sigs)
    # sse-stream
    ok, sigs = _returns_streaming_response(handler)
    if ok:
        return ("sse-stream", sigs)
    # webhook-receiver
    ok, sigs = _references_webhook_header(handler, file_name)
    if ok:
        return ("webhook-receiver", sigs)
    return None


def _dec_full_path(dec_info: dict) -> str:
    """Placeholder — we don't bind to router prefix at classify time,
    caller must pass the full path instead. Kept for symmetry only."""
    return dec_info.get("path", "")


def scan_file(py: pathlib.Path) -> list[Proposal]:
    try:
        text = py.read_text()
        tree = ast.parse(text, filename=str(py))
    except Exception:
        return []
    router_prefixes = _parse_router_prefixes(tree)
    if not router_prefixes:
        return []
    text_lines = text.splitlines()

    out: list[Proposal] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            info = _decorator_info(dec)
            if info is None:
                continue
            if info["router_var"] not in router_prefixes:
                continue
            prefix = router_prefixes[info["router_var"]]
            dec_path = info["path"]
            full = (prefix + dec_path) if prefix and dec_path != "/" else (
                prefix or dec_path or "/"
            )
            if not full.startswith("/"):
                full = "/" + full
            # Overwrite `path` with the FULL path for classifier use
            info_with_full = {**info, "path": full}
            if _has_existing_tag(text_lines, dec):
                continue
            result = _classify_handler(info_with_full, dec, node, py.name)
            if result is None:
                continue
            reason, signals = result
            out.append(Proposal(
                file=str(py.relative_to(BACKEND_ROOT)),
                decorator_line=dec.lineno,
                method=info["method"],
                path=full,
                reason=reason,
                signals=signals,
            ))
    return out


def apply_proposal(proposal: Proposal) -> bool:
    """Insert `# test-exempt: <reason>` comment on the decorator's
    closing-paren line (multi-line-decorator safe). Returns True on
    successful mutation."""
    py = BACKEND_ROOT / proposal.file
    try:
        text = py.read_text()
        tree = ast.parse(text, filename=str(py))
    except Exception:
        return False

    # Re-locate the decorator at its start lineno, find its end_lineno,
    # then mutate the end line to append `  # test-exempt: <reason>`.
    target_end = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if dec.lineno == proposal.decorator_line:
                target_end = getattr(dec, "end_lineno", dec.lineno)
                break
        if target_end:
            break
    if target_end is None:
        return False

    lines = text.splitlines(keepends=True)
    idx = target_end - 1
    if idx < 0 or idx >= len(lines):
        return False
    line = lines[idx]
    if "test-exempt:" in line:
        return False
    # Preserve trailing newline
    if line.endswith("\n"):
        core, nl = line[:-1], "\n"
    else:
        core, nl = line, ""
    new_line = f"{core.rstrip()}  # test-exempt: {proposal.reason}{nl}"
    lines[idx] = new_line
    py.write_text("".join(lines))
    return True


def main(argv: list[str]) -> int:
    apply_mode = "--apply" in argv

    all_proposals: list[Proposal] = []
    for py in sorted(BACKEND_API.rglob("*.py")):
        all_proposals.extend(scan_file(py))

    if not all_proposals:
        print("suggest_test_exempts: no auto-tag candidates detected")
        return 0

    by_reason: dict[str, list[Proposal]] = defaultdict(list)
    for p in all_proposals:
        by_reason[p.reason].append(p)

    print(f"# suggest_test_exempts — {len(all_proposals)} proposal(s)\n")
    for reason in sorted(by_reason):
        props = by_reason[reason]
        print(f"## {reason} — {len(props)} proposal(s)\n")
        for p in props:
            print(f"- `{p.method} {p.path}` — `{p.file}:{p.decorator_line}`")
            for sig in p.signals:
                print(f"    • {sig}")
        print()

    if apply_mode:
        applied = 0
        for p in all_proposals:
            if apply_proposal(p):
                applied += 1
        print(f"\n✓ Applied {applied}/{len(all_proposals)} proposals")
        return 0

    print("(Dry run — pass --apply to insert the tags.)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # pragma: no cover
        print(f"suggest_test_exempts: script error — {exc}", file=sys.stderr)
        sys.exit(2)
