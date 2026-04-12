# Breach Response Runbook — HedgeSpark

**Document version:** 1.0 (2026-04-11)
**Audience:** Founder / DPO / on-call engineer
**Regulation:** GDPR Art. 33 + Art. 34, CCPA §1798.82, LGPD Art. 48

GDPR requires supervisory-authority notification **within 72 hours** of
becoming aware of a personal-data breach. Missed deadlines carry fines
up to 2% of worldwide annual turnover or €10 M, whichever is higher.

This runbook is the step-by-step procedure the on-call engineer follows
the moment a `breach_response_required` alert lands in Telegram.

---

## 0. Automated context already in place

When a `breach_response_required` alert fires, the platform has already
done the following for you:

- The underlying `ops_alert` (the original signal) is still open and
  linked via `source=breach:<id>`.
- A classification is stamped into the `breach_response` payload
  (`classification`, `supervisory_deadline`, `data_subject_deadline`,
  `description`).
- An entry is written to the hash-chained `audit_log` so the clock
  officially started at the stamped timestamp.
- The founder is paged through the same Telegram channel the daily
  digest uses — you cannot miss it.

---

## 1. Triage within 30 minutes

- [ ] Acknowledge the alert on Telegram (the dashboard auto-marks it
      `ack` when the operator taps the inline keyboard).
- [ ] Open the dashboard `/ops/alerts` view and pull the original
      alert by id.
- [ ] Read the audit log for the last 7 days of actions by the same
      actor_name. Look for anomalies (unexpected apply, foreign IP,
      unusual hour).
- [ ] Decide: **confirmed breach**, **potential breach**, or
      **false positive**.

### 1a. False positive

- [ ] Resolve both the response alert and the underlying alert with
      `resolved_by='operator'` and a short reason.
- [ ] Write a post-mortem comment in `audit_log` via `/ops/alerts` —
      "why it looked like a breach and why it wasn't".
- [ ] STOP.

---

## 2. Contain — within 1 hour of confirmation

- [ ] If an operator key or merchant token is suspected leaked, **rotate
      immediately**:
      - Shopify access tokens: force a session bump (`merchants.session_version += 1`)
        so the next dashboard request forces re-authentication.
      - Operator `DASHBOARD_API_KEY`: set `DASHBOARD_API_KEY_PREV=<old>`, update
        `DASHBOARD_API_KEY=<new>` in `.env`, `pm2 restart wishspark-backend`.
      - Encryption key (`MERCHANT_TOKEN_ENCRYPTION_KEY`): only rotate under the
        key-rotation playbook — this requires re-encrypting every stored token.
- [ ] If the attack vector is a specific endpoint, set a rate limit or
      feature flag to disable it while the fix is developed.
- [ ] Pause the autonomous pipeline if the compliance score hasn't
      already auto-paused it: `redis-cli SET hs:compliance:auto_pause 1`.

---

## 3. Assess the blast radius

- [ ] Run the data-lineage query to determine which merchants' rows
      were accessed during the incident window.
      ```bash
      psql -c "SELECT shop_domain, action_type, created_at
               FROM audit_log
               WHERE created_at >= '<incident_start>'
                 AND actor_name = '<suspect_actor>'
               ORDER BY created_at"
      ```
- [ ] Determine whether personal data (contact_email, customer_email,
      visitor_id) was accessed or exfiltrated.
- [ ] Classify risk:
      - **Low** — Technical access only, no personal data touched.
      - **Medium** — Personal data read but no evidence of exfiltration.
      - **High** — Personal data exfiltrated or publicly exposed.

---

## 4. Notify supervisory authority (≤72h from awareness)

Mandatory when the breach is likely to result in a risk to rights and
freedoms of natural persons — i.e. anything rated **medium** or **high**
above.

Template (fill in and send to the supervisory authority of the lead
establishment; for HedgeSpark, this is the Italian Garante if the EU
lead is Italy):

```
To: <lead DPA>
Subject: Personal data breach notification — HedgeSpark

1. Nature of the breach:
2. Approximate number of data subjects:
3. Approximate number of records:
4. Contact details of the DPO:
5. Likely consequences:
6. Measures taken / proposed:
7. Date and time of awareness (from audit_log `breach_classified`):
```

Submit within 72 hours of the `classified_at` timestamp stamped on the
audit_log entry. If information is incomplete, submit what you have
and supplement later — Art. 33(4) allows phased notification.

---

## 5. Notify data subjects (Art. 34, when applicable)

Required only when the breach is likely to result in a **high** risk.
Communication must be:

- In clear and plain language.
- Describe the nature of the breach.
- Include the DPO contact.
- Describe likely consequences.
- Describe measures taken.

For HedgeSpark this typically means an email to every affected merchant
(and, when visitor data is affected, to the merchant who is the
controller for their visitors — it's the merchant's duty under Art. 34
to pass the notice to end-visitors).

The `email_orchestrator` has a dedicated `email_type='breach_notice'`
category that bypasses rate limits and suppression — see
`app/services/email_orchestrator.py`.

---

## 6. Post-mortem

Within 7 days of containment:

- [ ] Write a post-mortem in `docs/incidents/<YYYY-MM-DD>-<slug>.md`
      using the template.
- [ ] Identify the root cause (not the trigger).
- [ ] Propose prevention: either a new rule in
      `security_preflight_guard.py`, a new probe in
      `security_heartbeat.py`, a new signature in
      `breach_notification._BREACH_SIGNATURES`, or a compliance-score
      component that catches the same pattern earlier.
- [ ] Re-enable the autonomous pipeline only after the prevention
      shipped AND the compliance score recovers above 70.

---

## 7. Jurisdiction-specific variants

Worldwide deployment means different clocks in different jurisdictions.
When the breach affects data subjects in any of these regions, follow
BOTH the GDPR process above AND the local rule:

| Region | Regulator | Deadline | Notes |
|--------|-----------|----------|-------|
| EU / EEA | Lead DPA (per Art. 56) | 72h / without undue delay (Art. 34) | Main track |
| UK | ICO | 72h | Separate post-Brexit notification portal |
| California | Attorney General | "Most expedient time possible" | Required if > 500 residents affected (civil code §1798.82) |
| Brazil | ANPD | "Reasonable time" (LGPD Art. 48) | ANPD is still finalizing the exact deadline |
| South Korea | PIPC | 24h | Fastest clock in the world |
| China | CAC (PIPL Art. 57) | "Immediately" + report to provincial authority | Tight deadline + data localization rules |
| Japan | PPC (APPI) | "Promptly" | Required when breach risks individual rights |
| Australia | OAIC | "As soon as practicable" | Notifiable Data Breaches scheme |
| Canada | OPC | "As soon as feasible" | PIPEDA; failure is prosecutable |
| India | DPBI (DPDP Act 2023) | "Without undue delay" | New framework; evolving |

On-call engineer: err on the side of early notification. Late
notification penalties dwarf the cost of being overly cautious.

---

## 8. Contacts

| Role | Contact | Escalation |
|------|---------|------------|
| Founder | (Telegram `TELEGRAM_CHAT_ID`) | Primary decision-maker |
| DPO | (appoint before worldwide launch) | Legal counsel |
| Legal | (retain a privacy firm on retainer) | Regulator interface |
| Cloud ops | (VPS provider support) | Infrastructure isolation |

---

## 9. Drill schedule

- **Monthly**: `security_heartbeat` runs 24×7, surfacing false positives
  that the operator practices on.
- **Quarterly**: synthetic breach drill — operator manually triggers a
  `security_probe_failed` alert via the ops endpoint and runs through
  steps 1–4 end-to-end, stopping before actual regulator contact.
- **Annually**: full tabletop exercise with legal counsel.

The compliance score's `security_probes` component captures drill
participation; if probes aren't firing, the score drops and the
pipeline auto-pauses.
