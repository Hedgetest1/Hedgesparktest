"""
on_alert_triage_llm.py — B1 LLM triage call site for
`on_alert_responder`.

Converts a context packet (alert + recent related alerts + recent
commits + recent worker errors) into a structured triage verdict:

    {
        "severity": "P0" | "P1" | "P2",
        "probable_cause": "one-sentence hypothesis",
        "suggested_owner": "subsystem name" | "human",
        "triage_steps": ["step1", "step2", ...],   # 2-5 bullets
        "related_commits": ["sha ..."],            # 0-3 shas the LLM flags
        "requires_human_now": bool,
    }

Follows the same pattern as `orchestrator_llm`:
    1. Budget gate via check_budget("on_alert_responder")
    2. PII guard via assert_clean on the serialized context
    3. Model select via llm_router.select_model
    4. Anthropic primary, OpenAI fallback (both with 429 backoff)
    5. Record usage, enforce max_tokens

On any failure (no key, budget blocked, PII block, parse fail) returns
`None` so the caller treats the alert as "not triaged yet" and retries
on the next cycle. Never raises.

Scope restrictions (§10 TIER_1 — propose only):
    - READ-ONLY on system state.
    - Never proposes code patches (those go through bugfix_pipeline).
    - Writes only `audit_log` in the caller, not here.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

import httpx

log = logging.getLogger("on_alert_triage_llm")

_TIMEOUT = 20.0
_MODULE = "on_alert_responder"
_ALLOWED_SEVERITIES = {"P0", "P1", "P2"}

_SYSTEM_PROMPT = """You are the on-call triage assistant for HedgeSpark, a Shopify commerce intelligence SaaS.

You receive ONE production alert + recent related context. Your job is to classify the alert's severity, propose a probable cause, and suggest 2-5 concrete triage steps a human operator can act on. You are NOT allowed to propose code patches — the code-change pipeline handles that separately.

SEVERITY RULES:
- P0: merchant-impacting now OR within the hour; requires human action right now.
- P1: degraded state, not merchant-visible yet but likely to escalate if ignored for a few hours.
- P2: hygiene/debt signal, worth tracking but no SLA pressure.

TRIAGE STEP RULES:
- Each step must be concrete (runnable command, specific URL to check, specific query).
- Never propose running arbitrary code, modifying prod data, or bypassing safety gates.
- Cite a specific commit sha from "Recent commits (48h)" only if it is a plausible regression cause.
- If the context is insufficient to form a hypothesis, say so explicitly with one triage step: "collect more data before triaging" — do NOT invent a cause.

RESPONSE FORMAT (strict JSON, no prose outside):
{
  "severity": "P0" | "P1" | "P2",
  "probable_cause": "one sentence",
  "suggested_owner": "subsystem/module name or 'human'",
  "triage_steps": ["step 1", "step 2", "step 3"],
  "related_commits": ["sha", ...],
  "requires_human_now": true | false
}

The "requires_human_now" field is for P0 incidents — set true ONLY if a human needs to read this within 10 minutes. Setting it wakes up the founder via Telegram ping, so be conservative.
"""


@dataclass
class TriageVerdict:
    severity: str = "P2"
    probable_cause: str = ""
    suggested_owner: str = ""
    triage_steps: list[str] = field(default_factory=list)
    related_commits: list[str] = field(default_factory=list)
    requires_human_now: bool = False
    model_used: str = ""
    raw_response: str = ""
    error: str | None = None


def triage(context_packet: dict) -> TriageVerdict | None:
    """Run the LLM triage on a context packet. Returns a verdict on
    success, None on any failure path (no api key, budget blocked,
    pii block, model call failed, response unparseable). Never
    raises."""
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not anthropic_key and not openai_key:
        log.info("on_alert_triage_llm: no api key configured — skipping")
        return None

    # Budget gate
    try:
        from app.core.llm_budget import (
            check_budget, record_blocked, record_usage,
            is_provider_backed_off, record_429,
        )
    except Exception as exc:
        log.warning("on_alert_triage_llm: llm_budget import failed: %s", exc)
        return None
    allowed, reason = check_budget(_MODULE)
    if not allowed:
        record_blocked(_MODULE, reason)
        log.info("on_alert_triage_llm: budget blocked — %s", reason)
        return None

    # Serialize context with safe truncation so prompt-size doesn't blow
    # the max_tokens_per_request budget on a chatty alert.
    try:
        user_message = _format_context(context_packet)
    except Exception as exc:
        log.warning("on_alert_triage_llm: context format failed: %s", exc)
        return None

    # PII guard
    try:
        from app.core.llm_pii_guard import assert_clean, LLMPayloadViolation
        assert_clean(user_message, context=_MODULE)
    except LLMPayloadViolation as exc:
        log.error("on_alert_triage_llm: %s", exc)
        return None
    except Exception as exc:
        log.warning("on_alert_triage_llm: pii_guard check failed: %s", exc)
        # Fail-closed on unexpected PII guard failure — better to skip
        # this triage cycle than leak data upstream.
        return None

    # Select model + call
    from app.core.llm_router import select_model
    sel = select_model(
        module="orchestrator",  # mirror orchestrator routing for now
        anthropic_available=bool(anthropic_key) and not is_provider_backed_off("anthropic"),
        openai_available=bool(openai_key) and not is_provider_backed_off("openai"),
    )
    if sel.provider == "none":
        log.info("on_alert_triage_llm: no provider available after policy")
        return None

    raw = ""
    model = sel.model
    in_tok = 0
    out_tok = 0
    if sel.provider == "anthropic" and anthropic_key:
        raw, model, in_tok, out_tok = _call_anthropic(
            user_message, anthropic_key, model=sel.model,
            max_tokens=sel.max_tokens,
        )
        if not raw:
            record_429("anthropic") if sel.provider == "anthropic" else None
    if not raw and openai_key and not is_provider_backed_off("openai"):
        raw, model, in_tok, out_tok = _call_openai(
            user_message, openai_key,
            model="gpt-4o-mini", max_tokens=sel.max_tokens,
        )

    if not raw:
        log.warning("on_alert_triage_llm: no response from any provider")
        return None

    try:
        # Ground-truth token count from provider usage struct (2026-04-23
        # sweep). Fall back to len-estimate only if usage absent.
        _tokens = (in_tok + out_tok) or ((len(user_message) + len(raw)) // 4)
        record_usage(
            _MODULE, tokens_used=_tokens,
            provider=sel.provider, model=model,
        )
    except Exception:
        pass  # SILENT-EXCEPT-OK: usage accounting best-effort, request already completed

    verdict = _parse_verdict(raw, model)
    if verdict is None:
        log.warning(
            "on_alert_triage_llm: response parse failed — raw_head=%r",
            raw[:200],
        )
    return verdict


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

_MAX_CTX_CHARS = 6000


def _format_context(packet: dict) -> str:
    """Serialize the context packet into a budget-safe prompt body."""
    alert = packet.get("alert") or {}
    related = packet.get("related_alerts_48h") or []
    commits = packet.get("recent_commits_48h") or []
    worker_errors = packet.get("worker_errors_6h") or []

    parts: list[str] = [
        "## Alert",
        f"- id: {alert.get('id')}",
        f"- created_at: {alert.get('created_at')}",
        f"- severity: {alert.get('severity')}",
        f"- alert_type: {alert.get('alert_type')}",
        f"- shop_domain: {alert.get('shop_domain') or 'n/a'}",
        f"- summary: {alert.get('summary')}",
    ]
    detail = alert.get("detail")
    if isinstance(detail, (dict, list)):
        parts.append(f"- detail: {json.dumps(detail)[:800]}")
    elif detail:
        parts.append(f"- detail: {str(detail)[:800]}")

    if related:
        parts.append("\n## Related alerts (same type, last 48h)")
        for r in related[:10]:
            parts.append(
                f"- {r.get('created_at')} "
                f"severity={r.get('severity')} "
                f"resolved={r.get('resolved')} :: "
                f"{(r.get('summary') or '')[:120]}"
            )

    if commits:
        parts.append("\n## Recent commits (48h)")
        for c in commits[:20]:
            parts.append(f"- {c[:160]}")

    if worker_errors:
        parts.append("\n## Recent worker errors (6h)")
        for w in worker_errors[:10]:
            parts.append(
                f"- {w.get('started_at')} "
                f"worker={w.get('worker_name')} "
                f"errors={w.get('errors')} :: "
                f"{(w.get('error_detail') or '')[:120]}"
            )

    body = "\n".join(parts)
    if len(body) > _MAX_CTX_CHARS:
        body = body[:_MAX_CTX_CHARS] + "\n[context truncated]"
    return body


# ---------------------------------------------------------------------------
# Response parse
# ---------------------------------------------------------------------------

def _parse_verdict(raw: str, model: str) -> TriageVerdict | None:
    """Strict JSON parse with graceful fallback on model-wrapped JSON."""
    text = (raw or "").strip()
    if not text:
        return None
    # Models occasionally wrap JSON in ```json fences; peel them off.
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
        # Trailing triple-backtick is rare after the first strip, but
        # safe to strip again.
        text = text.strip("`").strip()
    try:
        data = json.loads(text)
    except Exception:
        # Try to find the first {...} block if the model prepended prose.
        import re
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except Exception:
            return None

    if not isinstance(data, dict):
        return None

    severity = str(data.get("severity") or "").strip().upper()
    if severity not in _ALLOWED_SEVERITIES:
        # Lenient: accept lowercase "p0" etc., reject anything else.
        return None

    steps = data.get("triage_steps") or []
    if not isinstance(steps, list):
        steps = []
    steps = [str(s)[:300] for s in steps[:5] if s]

    commits = data.get("related_commits") or []
    if not isinstance(commits, list):
        commits = []
    commits = [str(c)[:80] for c in commits[:3] if c]

    return TriageVerdict(
        severity=severity,
        probable_cause=str(data.get("probable_cause") or "")[:500],
        suggested_owner=str(data.get("suggested_owner") or "")[:120],
        triage_steps=steps,
        related_commits=commits,
        requires_human_now=bool(data.get("requires_human_now")),
        model_used=model,
        raw_response=raw[:2000],
    )


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

def _call_anthropic(
    user_message: str, api_key: str, *,
    model: str, max_tokens: int,
) -> tuple[str, str, int, int]:
    """Returns (text, model, input_tokens, output_tokens). Ground-truth
    tokens from Anthropic's `usage` struct (2026-04-23 sweep)."""
    try:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "temperature": 0.1,
                "system": _SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_message}],
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            # Truncation rejection — 2026-04-23 sweep.
            if data.get("stop_reason") == "max_tokens":
                log.warning("on_alert_triage_llm: anthropic TRUNCATED at max_tokens=%d", max_tokens)
                return "", model, 0, 0
            txt = data.get("content", [{}])[0].get("text", "")
            _usage = data.get("usage") or {}
            return txt, model, int(_usage.get("input_tokens") or 0), int(_usage.get("output_tokens") or 0)
        if resp.status_code == 429:
            from app.core.llm_budget import record_429
            record_429("anthropic")
            return "", model, 0, 0
        log.warning("on_alert_triage_llm: anthropic status=%d", resp.status_code)
        return "", model, 0, 0
    except Exception as exc:
        log.warning("on_alert_triage_llm: anthropic failed: %s", type(exc).__name__)
        return "", model, 0, 0


def _call_openai(
    user_message: str, api_key: str, *,
    model: str, max_tokens: int,
) -> tuple[str, str, int, int]:
    """Returns (text, model, input_tokens, output_tokens). OpenAI names
    these prompt_tokens / completion_tokens in its usage struct."""
    try:
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "temperature": 0.1,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            choice = data.get("choices", [{}])[0]
            # Truncation rejection — 2026-04-23 sweep.
            if choice.get("finish_reason") == "length":
                log.warning("on_alert_triage_llm: openai TRUNCATED at max_tokens=%d", max_tokens)
                return "", model, 0, 0
            txt = choice.get("message", {}).get("content", "")
            _usage = data.get("usage") or {}
            return txt, model, int(_usage.get("prompt_tokens") or 0), int(_usage.get("completion_tokens") or 0)
        if resp.status_code == 429:
            from app.core.llm_budget import record_429
            record_429("openai")
            return "", model, 0, 0
        log.warning("on_alert_triage_llm: openai status=%d", resp.status_code)
        return "", model, 0, 0
    except Exception as exc:
        log.warning("on_alert_triage_llm: openai failed: %s", type(exc).__name__)
        return "", model, 0, 0
