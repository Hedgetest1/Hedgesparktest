# Sub-processors — HedgeSpark

**Document version:** 1.0 (2026-04-11)
**Regulation:** GDPR Art. 28

This list is mandatory under GDPR and must be kept up-to-date any time
HedgeSpark engages a new processor that touches personal data.
Merchants (controllers) are entitled to a current copy on request.

---

## Required agreements per processor

Every entry below has (or needs) on file:

1. A signed **Data Processing Agreement (DPA)** under Art. 28(3).
2. **Standard Contractual Clauses (SCCs)** when the processor is
   located outside the EEA (US-based providers).
3. A documented **scope of processing**: what categories, what purpose,
   what retention.
4. A **sub-processor list** from the processor itself (they may have
   onward transfers we need to disclose to merchants).

---

## Current processor register

| # | Processor | Location | Purpose | Personal data categories | DPA | SCCs | Status |
|---|-----------|----------|---------|---------------------------|-----|------|--------|
| 1 | **Shopify** | CA / US / EU | Merchant OAuth + storefront hosting + pixel data feed | Merchant tokens, order identifiers, customer email via pixel | Shopify DPA auto-accepted at Partner signup | Yes (Shopify SCCs 2021/914) | ✅ In place |
| 2 | **Resend** | US (Delaware) | Transactional email delivery (digests, lifecycle, GDPR export auto-delivery) | Merchant contact email, customer email when delivering an Art. 15 export | ⚠️ NEEDED — sign the DPA in the Resend dashboard before next prod release | Yes (Resend SCCs) | ⚠️ Pending sign |
| 3 | **Anthropic** | US (California) | LLM patch proposals + rare nudge composition (governed by `llm_budget`) | **Zero PII.** Only aggregated metrics + file manifests from the codebase. Verified at every call site. | Anthropic Data Processing Addendum | Yes | ⚠️ Pending sign |
| 4 | **OpenAI** | US (California) | Fallback LLM + nudge composition when Anthropic unavailable | Same as Anthropic — aggregated only | OpenAI DPA | Yes | ⚠️ Pending sign |
| 5 | **Sentry (Functional Software Inc.)** | US (California) | Backend + frontend error tracking | Stack traces with shop_domain + request_id — **`send_default_pii=False` is enforced in `app/main.py`** | Sentry DPA | Yes | ✅ In place (config) / ⚠️ Verify signed copy |
| 6 | **PostgreSQL (self-hosted)** | EU (current VPS) | Primary data store | Everything HedgeSpark holds | N/A — operated in-house | N/A | ✅ |
| 7 | **Redis (self-hosted)** | EU (current VPS) | Caching, rate limiting, identity bridge, ops state | Pseudonymous visitor IDs, short-lived auth nonces, metric counters | N/A — operated in-house | N/A | ✅ |
| 8 | **Let's Encrypt / Traefik** | EU (VPS) | TLS termination | None | N/A | N/A | ✅ |

---

## Action items (2026-04-11)

1. **Resend DPA** — sign in the Resend dashboard. Blocker for every
   production send.
2. **Anthropic DPA** — claim the addendum at the Anthropic Console.
3. **OpenAI DPA** — enterprise DPA (or API DPA for non-enterprise).
4. **Sentry DPA** — locate the signed copy or re-sign via the org
   settings.
5. Add a footer link to `app.hedgesparkhq.com/privacy/processors` that
   renders this document for merchants on request.

---

## When adding a new processor

Before engaging any third-party service that will touch personal data,
complete this checklist:

- [ ] DPA signed and stored in the compliance vault.
- [ ] SCCs attached if the processor is outside the EEA.
- [ ] Scope of processing documented here (categories, purpose,
      retention).
- [ ] `docs/DPIA.md` updated if the risk register changes.
- [ ] `/docs/processors.md` (this file) bumped with a new row.
- [ ] Notice to merchants (if they're under a 30-day prior-notice
      obligation) scheduled.

Adding a processor without completing the above is a TIER_2 operation
and must be approved by the founder.

---

## Last reviewed
2026-04-11
