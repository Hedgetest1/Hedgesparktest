# Resend DNS runbook — recover `@hedgesparkhq.com` email flow

> **When to use this:** you saw `/ops/email-health` return `verified: false`,
> or a 🔴 Telegram alert said "Resend DNS FAILED", or preflight printed
> `WARN: Resend domain hedgesparkhq.com status=failed`.
>
> **Why this matters:** until the Resend DNS check passes, EVERY email
> through `dev@hedgesparkhq.com` / `digest@hedgesparkhq.com` /
> `andrea@hedgesparkhq.com` is silently dropped by Resend post-API.
> Morning briefs, weekly digests, monthly ROI, breach notifications —
> all suppressed. The runtime gate in `send_email()` short-circuits the
> API call so we at least don't burn quota + pollute `merchant_emails`.
>
> **TIER:** operational (no code change). Total time: ~15 minutes.

## 1 — Verify the current state

```bash
# Terminal on the backend server, reading RESEND_API_KEY from .env:
curl -s -H "Authorization: Bearer $(grep ^RESEND_API_KEY /opt/wishspark/backend/.env | cut -d= -f2)" \
  https://api.resend.com/domains/b65abad8-43f3-4dfe-aaa7-29b62a701495 \
  | python3 -m json.tool | head -30
```

Look at `"status"`:

| Value | Meaning |
|---|---|
| `verified` | ✅ Nothing to do — email flowing. |
| `failed` | ❌ DNS records missing or incorrect at the registrar. Proceed with the steps below. |
| `pending` | ⏳ DNS records were just added; Resend hasn't re-checked yet. Wait 5 min + re-run the curl. |

## 2 — Add / repair the DNS records at Hostinger

HedgeSpark's domain is registered at **Hostinger** (not Cloudflare).
Nameservers point to `dns-parking.com`; MX points to `hostinger.com`.

**Log in:**

1. Open `https://hpanel.hostinger.com/` and log in.
2. Navigate to `Domains → hedgesparkhq.com → DNS / Nameservers`.

**Add these three records** (exact values from the Resend API — do not
edit them):

| Type | Name | Value | Priority | TTL |
|---|---|---|---|---|
| `TXT` | `resend._domainkey` | `v=DKIM1; k=rsa; p=MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDAUvzm0LoxudxEXjDQPcciK4P4jnyqqAQ+CKzwVw5nh2HyVI/32MjBzgyJWv3hseu02mWfl0T5CfYvdBRDCI/Sj48ZIaZ5TsHmPiUTvBvdfjDsjsBOsAJ5GMA/veJK/mlxGC5fEWWzo5g8ZnegdPyrKOIXQmThsGA8EgMBhD7mRQIDAQAB` | — | Auto |
| `MX` | `send` | `feedback-smtp.eu-west-1.amazonses.com` | `10` | Auto |
| `TXT` | `send` | `v=spf1 include:amazonses.com ~all` | — | Auto |

Save after each record. Hostinger propagates to `dns-parking.com` within
~5 minutes (rarely longer).

> **⚠️ DKIM whitespace trap** (lesson learned 2026-04-22).
> When pasting the DKIM `p=` value into Hostinger's TXT field, make sure
> there is **no embedded whitespace** inside the base64. Resend's
> verifier is lax (accepts spaces + missing tags), **Gmail's verifier is
> strict**. A corrupted DKIM record will flip Resend to `verified=true`
> while Gmail silent-drops every email with zero visibility.
>
> The `v=DKIM1; k=rsa;` tag prefix is technically optional for Resend
> but strongly recommended — some strict DMARC verifiers ignore records
> without it. Paste in one continuous string, no line breaks, no spaces
> inside the `p=` base64 body.
>
> The `audit_email_deliverability.py` preflight now catches this class
> (strict base64 + whitespace rejection) and WARNs with the exact
> remediation.

## 3 — Re-verify

```bash
curl -s -H "Authorization: Bearer $(grep ^RESEND_API_KEY /opt/wishspark/backend/.env | cut -d= -f2)" \
  https://api.resend.com/domains/b65abad8-43f3-4dfe-aaa7-29b62a701495 \
  | python3 -m json.tool | head -8
```

When `"status": "verified"` appears, the hourly agent_worker task
(`_run_email_dns_status_check`) will detect the flip within 60 minutes
and fire a 🟢 Telegram alert. If you want instant confirmation:

```bash
# Force the cache refresh by calling the ops endpoint with the API key:
curl -s -H "X-API-Key: $DASHBOARD_API_KEY" \
  https://api.hedgesparkhq.com/ops/email-health | python3 -m json.tool
```

You should see `"verified": true` within seconds of the Resend status
flipping.

## 4 — Backfill check

After the domain re-verifies, any email that was suppressed while DNS
was broken is lost — Resend does not retry and our orchestrator logs
the suppression as `send_failed` (or the new `DNS_SUPPRESSED` log line).
Expected behavior: the NEXT scheduled cycle (morning brief at 08:00 Rome,
weekly digest at the next configured cadence) resumes normally.

If you want to confirm sends are flowing again:

```bash
# 24h after the re-verify, read the merchant_emails table:
psql $DATABASE_URL -c "
  SELECT DATE(created_at), status, COUNT(*)
  FROM merchant_emails
  WHERE created_at > now() - interval '24 hours'
  GROUP BY 1, 2 ORDER BY 1 DESC, 2;
"
```

`status=sent` rows appearing after the re-verify timestamp confirm the
pipe is live.

## Why the preventer cannot fix this itself

The self-healing pipeline can only rewrite **code** it owns. DNS lives
at the registrar, outside every HedgeSpark repo or process. The preventer
therefore focuses on the three things it CAN do:

1. **Stop the bleeding** — short-circuit `send_email()` while DNS is
   failed so we don't burn Resend API quota on guaranteed-failed sends.
2. **Stay visible** — every preflight prints the state; the
   `/ops/email-health` endpoint exposes it in JSON; the hourly agent
   task alerts on flip.
3. **Auto-recover instantly** — the moment the founder fixes DNS,
   the cache refresh picks it up within 60 minutes and the next email
   cycle goes back to normal with zero further action.

---

**Last verified:** 2026-04-22 (file born).
**Related files:**
- `app/services/email_deliverability.py` — cache + org-domain gate
- `app/core/email.py::send_email()` — runtime suppression
- `app/workers/tasks/email_dns_status_task.py` — hourly flip detection
- `app/api/ops.py::get_email_health()` — `/ops/email-health`
- `scripts/audit_email_deliverability.py` — preflight WARN
