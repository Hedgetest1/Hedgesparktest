# Data Protection Impact Assessment — HedgeSpark

**Document version:** 1.0 (2026-04-11)
**Controller:** HedgeSpark (SaaS provider) + the merchant (Shopify shop)
**Status:** Living document. Re-assess whenever a new personal-data
flow is added.

GDPR Art. 35 requires a DPIA whenever processing is "likely to result
in a high risk to the rights and freedoms of natural persons." The
triggers that apply to HedgeSpark:

1. **Large-scale systematic monitoring of public areas** — the storefront
   tracker (`spark-tracker.js`) observes every visitor on a merchant's
   Shopify site.
2. **Automated profiling** — per-visitor intent scoring and per-merchant
   Revenue-at-Risk Score (RARS) compute behavioral scores that drive
   targeted actions (nudges, alerts, prioritization).
3. **Cross-border data transfer** — the platform relies on US-based
   sub-processors for email delivery, error tracking, and optional LLM
   features.

This DPIA documents the scope, risks, mitigations, and residual-risk
rating for each of those processing activities.

---

## 1. Scope of processing

### 1.1 Data sources
| Source                | Personal data categories | Legal basis |
|-----------------------|---------------------------|--------------|
| Storefront tracker (`spark-tracker.js`) | pseudonymous visitor ID, URL path, referrer, UTM params, device type, dwell seconds, scroll depth | Consent (Art. 6(1)(a)) via the merchant's cookie banner; legitimate interest (Art. 6(1)(f)) for security/fraud events only |
| Shopify orders / pixel | order ID, total, currency, customer email (hashed before LLM use) | Contract (Art. 6(1)(b)) with the merchant; pixel events require consent |
| Merchant dashboard    | merchant contact email, billing status, plan tier | Contract (Art. 6(1)(b)) |
| Support inbox         | inbound merchant emails (helpdesk) | Legitimate interest (Art. 6(1)(f)) |

### 1.2 Data subjects
- **End-visitors** of Shopify storefronts — primarily EU/EEA residents when the merchant is EU-based.
- **Merchants** — Shopify shop operators; mostly B2B, but sole traders are natural persons.
- **Support contacts** — individuals who email HedgeSpark support.

### 1.3 Processing operations
- Collection via REST `POST /track` and Shopify webhooks (`orders/*`)
- Storage in Postgres (`events`, `visitor_purchase_sessions`, `shop_orders`, etc.)
- Short-term caching in Redis (identity bridge, rate limits, RARS history)
- Automated computation (intent score, RARS, refund loss, nudge targeting)
- Outbound delivery: weekly digest emails, lifecycle emails, follow-up
- Optional LLM enrichment (Anthropic / OpenAI) — **aggregated metrics only, never raw PII**
- Manual operator review on `app.hedgesparkhq.com/ops/*` (authenticated)

### 1.4 Retention (enforced by `app/services/data_retention.py`)
- `events`: **395 days** (13 months) — see `DATA_RETENTION_EVENTS_DAYS`
- `visitor_purchase_sessions`: **730 days** (24 months) — see `DATA_RETENTION_VPS_DAYS`
- `shop_orders`: indefinite while shop is active (financial record retention exception, Art. 17(3)(b)); deleted on `shop/redact`
- Redis keys: all have explicit TTL (`hs:symap:*` 90d, `hs:rl:*` 60s, etc.)
- Tracker-level consent denial: event never persists (GDPR Art. 6/7)

### 1.5 Automated decision-making (Art. 22)
Intent scoring and RARS are inputs to targeted nudge placement. Because
nudge placement does NOT produce legal or similarly significant effects
on the visitor (no price discrimination, no access denial, no lending
decision), the operations sit outside Art. 22's "solely automated
decisions" prohibition. Merchants nevertheless must disclose the
scoring in their own privacy policy — HedgeSpark provides a template.

Opt-out: merchants may flip `opt_out_automated_targeting` (planned —
see roadmap item post-2026-04-11).

---

## 2. Necessity & proportionality

HedgeSpark's value proposition — loss-prevention, holdout-measured fixes,
peer benchmarks — cannot be delivered without observing visitor
behavior on a merchant's own storefront. The processing is necessary
for the service the merchant explicitly purchased. Data minimization is
enforced by:

- Pseudonymous visitor IDs (not names, not IPs, not email unless Shopify
  pixel provides it).
- No IP address storage (absent from `events` schema).
- Masking of contact emails in all operator-facing logs
  (`app/core/privacy.py:mask_email`).
- Aggregated metrics only to LLMs — raw events, emails, and PII never
  leave our infrastructure for inference calls.

---

## 3. Risk register

| # | Risk | Likelihood | Impact | Net | Mitigation |
|---|------|-----------|--------|-----|------------|
| R1 | Re-identification of visitors via URL + UTM + dwell patterns | Medium | Medium | Medium | Retention ceiling; scheduled deletion; no IP stored; Art. 22 disclosure template |
| R2 | Unauthorized access to merchant dashboard | Low | High | Medium | Session cookie with SameSite=None+HttpOnly+Secure; CSRF-hardened OAuth state (2026-04-11); timing-safe operator API key comparison; tenant-scoped queries |
| R3 | Sub-processor breach (Resend / Anthropic / OpenAI / Sentry) | Low | High | Medium | DPAs + SCCs required on every processor; aggregated-only data to LLMs; Sentry `send_default_pii=False` |
| R4 | SLA breach on GDPR request (Art. 15/17, Shopify 48h) | Medium | High | High | Deadline-enforced `gdpr_sla.enforce_sla` emits CRITICAL ops_alerts; `/ops/gdpr/*` dashboard surfaces queue depth |
| R5 | PII leakage via logs / Sentry / crash dumps | Low | Medium | Low | `mask_email` helper applied at every log call site; grep-verified |
| R6 | Consent-less tracking of EU visitors | Medium | High | High | `/track` consent gate; events explicitly marked denied are dropped; metric counter for consent rejection rate |
| R7 | Indefinite data accumulation | Low | Medium | Low | Retention worker runs daily; env-tunable TTLs; kill switch `DATA_RETENTION_PAUSED` |
| R8 | Self-modifying pipeline introduces a security/privacy regression | Low | Critical | Medium | TIER_2 files are apply-gated (human approval required); preflight guard scans proposed diffs for PII-in-logs, weakened HMAC, SQL injection, rate-limit removal (see security-aware preflight guard 2026-04-11) |

---

## 4. Residual risk

Highest residuals after mitigation: **R4 (SLA breach)** and **R6
(consent)**. Both are covered by automated guardrails (SLA enforcement
worker, consent gate) but rely on the merchant to configure their
consent banner correctly. We document this dependency in the merchant
onboarding guide and surface the consent-accepted/denied ratio per
merchant in the dashboard.

All other risks are rated **Low** after mitigation.

---

## 5. Review triggers

Re-run this DPIA when any of the following occurs:
1. A new data source is added to the tracker or webhook layer.
2. A new sub-processor is engaged (update `docs/processors.md` first).
3. A new automated decision emerges that could produce legal or
   similarly significant effects on data subjects (e.g. automated
   refund approval, automated buyer risk scoring).
4. A GDPR enforcement action targets a SaaS in the same category.
5. The retention policy changes.

---

## 6. Sign-off

- **Controller representative:** Founder / DPO (when appointed)
- **Technical review:** Backend lead + autonomous pipeline (self-debugging)
- **Last reviewed:** 2026-04-11
- **Next scheduled review:** 2026-10-11 (6 months)
