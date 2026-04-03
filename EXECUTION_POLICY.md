# HedgeSpark — Execution Policy for Autonomous Agents

Version: 1.0
Status: Active
Applies to: All AI agents operating on this codebase (Claude Code, bugfix pipeline, evolution engine, meta-reviewer, orchestrator)

---

## 1. Tier Model

Every code change and operational action falls into exactly one tier.

### TIER_0 — Autonomous (agent may apply without human approval)

**Scope:** Changes where a mistake is cheap, reversible, and caught by automated checks.

**Allowed targets:**
- `tests/*` — test files (add, fix, extend)
- `docs/*` — auto-generated docs (AUTO_CONTEXT.md, SERVER_CONTEXT.md)
- `app/services/*` — business logic services (EXCEPT sensitive services, see §2)
- `app/api/*` — API endpoints (EXCEPT oauth, billing, webhooks — see TIER_2)
- `app/workers/*` — background workers
- `dashboard/src/*` — frontend components

**Required checks before applying:**
1. `pytest tests/ --ignore=tests/test_scaling_intelligence.py -q` — must pass, count must not drop below 631
2. If frontend changed: `cd /opt/wishspark/dashboard && npx next build` — must succeed
3. If tracker changed: bump `TRACKER_VERSION` in `app/core/tracker_version.py`
4. `git diff --stat` — verify blast radius is proportional to intent (no accidental bulk changes)

**Constraints:**
- Max 5 files changed per autonomous patch
- No changes to function signatures used by 3+ callers without TIER_1 escalation
- No deletion of exported functions/classes without grep confirming zero external usage
- Each change must include or pass existing tests covering the modified code path

### TIER_1 — Propose + Human Approval Required

**Scope:** Changes where a mistake has moderate blast radius, affects merchant experience, or touches integration boundaries.

**Allowed targets (propose only, do not apply):**
- `tracker/*.js` — storefront scripts (runs in merchant browsers)
- `app/services/orchestrator.py` — action execution logic
- `app/services/orchestrator_llm.py` — LLM decision layer
- `app/services/bugfix_pipeline.py` — self-modification pipeline
- `app/services/promotion_pipeline.py` — code promotion logic
- `app/services/evolution_engine.py` — self-improvement proposals
- `app/services/reviewer_layer.py` — review verdict logic
- `app/services/project_brain.py` — strategic constitution and domain mapping
- `app/services/meta_reviewer.py` — strategic prioritization
- `app/services/merge_intelligence.py` — merge decision logic
- `app/core/llm_budget.py` — spend caps and limits
- `app/core/llm_router.py` — model routing
- `app/core/ai_router.py` — AI routing
- `app/core/rate_limit.py` — rate limiting rules
- `app/core/alert_delivery.py` — external notification logic
- `app/models/*` — SQLAlchemy model definitions (column additions, constraint changes)
- `ecosystem.config.js` — PM2 process configuration
- Multi-file refactors touching 6+ files
- Any change that modifies the audit_log schema or write path

**Proposal format:**
```
TIER_1 PROPOSAL
Target: <file path(s)>
Domain: <domain from §3>
Change: <1-2 sentence description>
Risk: <low|medium|high>
Blast radius: <number of files, affected domains>
Tests: <passing|failing|new tests needed>
Rollback: <how to revert if wrong>
```

**Approval path:** Human reviews proposal → approves via Telegram (`/approve`) or ops API (`POST /ops/approvals/{id}/approve`) → agent applies with full verification.

### TIER_2 — Human-Only (agent must never modify)

**Scope:** Changes where a mistake causes data loss, security breach, merchant lockout, or billing corruption.

**Protected files (absolute — no exceptions):**
- `app/core/token_crypto.py` — AES-256-GCM token encryption
- `app/core/merchant_session.py` — JWT session signing
- `app/core/deps.py` — auth middleware and dependency injection
- `app/api/shopify_oauth.py` — OAuth install/callback flow
- `app/api/billing.py` — Shopify billing lifecycle
- `app/api/webhooks.py` — order ingestion + GDPR webhook handlers
- `app/services/shopify_auth.py` — token validation
- `app/services/order_ingestion.py` — revenue data pipeline
- `app/services/gdpr_processor.py` — GDPR compliance processing
- `migrations/versions/*` — all migration files (create or modify)
- `.env` — production secrets
- `deploy.sh` — deployment script
- `EXECUTION_POLICY.md` — this file

**Protected operations (absolute — no exceptions):**
- `git push --force` or `git reset --hard`
- `alembic upgrade` / `alembic downgrade` (running migrations)
- Modifying, creating, or deleting `.env` files
- Any direct database writes outside application code (raw SQL, psql)
- `pm2 restart` / `pm2 delete` / `pm2 stop` (use deploy.sh instead)
- Deleting git branches that exist on remote
- Any action that sends money, creates charges, or modifies billing state

**What agents CAN do with TIER_2 areas:**
- Read these files for context
- Report bugs or inconsistencies found in these files
- Suggest changes as plain-text recommendations (not patches, not diffs)

---

## 2. Domain Classification

Each domain has a criticality level that determines its default tier.

| Domain | Criticality | Default Tier | Files |
|--------|------------|-------------|-------|
| billing | CRITICAL | TIER_2 | `app/api/billing.py`, `app/services/billing*` |
| auth | CRITICAL | TIER_2 | `app/core/token_crypto.py`, `app/core/merchant_session.py`, `app/core/deps.py` |
| shopify_oauth | CRITICAL | TIER_2 | `app/api/shopify_oauth.py`, `app/services/shopify_auth.py` |
| webhooks | CRITICAL | TIER_2 | `app/api/webhooks.py`, `app/services/order_ingestion.py` |
| gdpr | CRITICAL | TIER_2 | `app/services/gdpr_processor.py`, `app/workers/gdpr_worker.py` (worker is TIER_0, processor is TIER_2) |
| migrations | HIGH | TIER_2 | `migrations/versions/*` |
| infra | HIGH | TIER_2 | `.env`, `ecosystem.config.js`, `deploy.sh` |
| orchestrator | HIGH | TIER_1 | `app/services/orchestrator*.py` |
| autofix | HIGH | TIER_1 | `app/services/bugfix_pipeline.py`, `app/services/promotion_pipeline.py`, `app/services/merge_intelligence.py` |
| model_governance | HIGH | TIER_1 | `app/services/model_config.py`, `app/services/model_upgrade_agent.py` |
| llm_infra | HIGH | TIER_1 | `app/core/llm_budget.py`, `app/core/llm_router.py` |
| reviewer | MEDIUM | TIER_1 | `app/services/reviewer_layer.py`, `app/services/project_brain.py`, `app/services/meta_reviewer.py` |
| tracker | MEDIUM | TIER_1 | `tracker/*.js` |
| models | MEDIUM | TIER_1 | `app/models/*` (schema definitions) |
| merchant_api | MEDIUM | TIER_0 | `app/api/dashboard.py`, `app/api/setup.py`, `app/api/auth.py`, etc. |
| nudges | MEDIUM | TIER_0 | `app/api/nudges.py`, `app/services/nudge_*.py` |
| workers | MEDIUM | TIER_0 | `app/workers/*` |
| intelligence | LOW | TIER_0 | `app/services/intent_engine.py`, `app/services/revenue_*.py`, `app/services/cohort_*.py`, etc. |
| tracking | LOW | TIER_0 | `app/api/track.py`, `app/services/event*` |
| observability | LOW | TIER_0 | `app/services/alerting.py`, `app/services/audit.py`, `app/services/system_*.py`, `app/services/telegram_agent.py` |
| support | LOW | TIER_0 | `app/api/chat_support.py`, `app/services/merchant_chatbot.py` |
| frontend | LOW | TIER_0 | `dashboard/src/*` |
| tests | LOW | TIER_0 | `tests/*` |
| docs | LOW | TIER_0 | `docs/*`, `SERVER_CONTEXT.md`, `AUTO_CONTEXT.md` |

**Tier override rule:** If a change touches files from multiple domains, the highest tier wins. A TIER_0 service change that also modifies a TIER_1 model file becomes TIER_1.

---

## 3. Pre-Action Checklist

Every agent must verify these conditions before acting. Failure on any item = STOP.

### Before ANY code change:
- [ ] Read the target file(s) first — never modify unread code
- [ ] Confirm the change falls within your tier authorization
- [ ] Check `git status` — no uncommitted work from another agent/human that could conflict

### Before applying a TIER_0 change:
- [ ] All pre-action checks above
- [ ] Write or verify test coverage for the changed code path
- [ ] Run `pytest tests/ --ignore=tests/test_scaling_intelligence.py -q` — PASS, count ≥ 631
- [ ] If frontend: `npx next build` — PASS
- [ ] If tracker: bump TRACKER_VERSION
- [ ] `git diff --stat` — max 5 files, changes match intent
- [ ] No function signature changes affecting 3+ callers

### Before proposing a TIER_1 change:
- [ ] All pre-action checks
- [ ] Generate proposal in the format specified in §1
- [ ] Include rollback plan
- [ ] Submit for human approval — do NOT apply

### Before requesting a TIER_2 change:
- [ ] All pre-action checks
- [ ] Describe the problem and suggested fix as plain text
- [ ] Do NOT generate diffs, patches, or edit commands for TIER_2 files
- [ ] Flag to human via Telegram or ops API

---

## 4. Escalation Triggers

An agent must STOP and escalate to human immediately when:

| Condition | Action |
|-----------|--------|
| Test count drops below 631 | STOP all changes. Report. Do not attempt to fix by deleting tests. |
| `/system/health` returns `"critical"` | STOP. Report via Telegram. Do not restart services. |
| Any TIER_2 file needs modification | STOP. Report as plain-text recommendation. |
| Change touches 3+ domains | STOP. Escalate as TIER_1 minimum regardless of individual domain tiers. |
| Unsure about tenant isolation (shop_domain scoping) | STOP. Ask before proceeding. |
| LLM budget monthly cap reached | STOP all LLM-dependent operations. Report. |
| Merge conflict on protected branch | STOP. Do not force-resolve. Report. |
| Agent detects it is modifying its own governance logic | STOP. Self-modification of orchestrator, reviewer, project_brain, or this policy = TIER_1 minimum. |
| Deploy failure (deploy.sh exit code ≠ 0) | STOP. Do not retry. Do not manually restart. Report. |
| Data deletion or destructive DB operation detected | STOP. Never execute. Report. |

---

## 5. Rollback Policy

### Autonomous rollback (agent may execute):
- `git checkout -- <file>` for uncommitted TIER_0 changes that broke tests
- Revert the most recent agent-authored commit if tests fail after commit (single `git revert HEAD`, not force push)

### Human-approved rollback only:
- `deploy.sh --rollback` (reverts running code to previous version)
- `pm2 restart` of any process
- `alembic downgrade` (schema rollback)
- `git revert` of commits older than HEAD
- Any rollback affecting TIER_2 files or domains

### Never (no agent, no automation):
- `git push --force`
- `git reset --hard`
- `DROP TABLE`, `TRUNCATE`, or destructive SQL
- Deleting `.env` or secrets files

---

## 6. Bugfix Pipeline Policy

The bugfix pipeline (`app/services/bugfix_pipeline.py`) follows this lifecycle:

```
detect → triage → propose → review → approve → apply → verify → promote
```

| Stage | Tier | Who |
|-------|------|-----|
| detect (scan for issues) | TIER_0 | Automated — bugfix pipeline worker |
| triage (classify severity) | TIER_0 | Automated — deterministic rules |
| propose (generate patch) | TIER_0 | Automated — LLM generates candidate (budget-gated) |
| review (assess patch) | TIER_0 | Automated — reviewer_layer deterministic assessment |
| approve | **TIER_1** | **Human required** — via `/bugfix_approve` or ops API |
| apply (write patch to disk) | TIER_0 | Automated — only after human approval + tests pass |
| verify (run tests post-apply) | TIER_0 | Automated — pytest must pass, count must not drop |
| promote (commit + deploy) | **TIER_1** | **Human required** — via `/merge` or ops API |

**Hard rules:**
- A bugfix patch is NEVER applied without human approval
- A bugfix commit is NEVER pushed without human approval
- If post-apply tests fail, auto-revert the patch (TIER_0 rollback)
- Patches targeting TIER_2 files are auto-rejected at the review stage

---

## 7. Evolution Engine Policy

The evolution engine (`app/services/evolution_engine.py`) follows this lifecycle:

```
scan → propose → review → accept → implement → verify → promote
```

| Stage | Tier | Who |
|-------|------|-----|
| scan (identify improvements) | TIER_0 | Automated — weekly scan |
| propose (draft proposal) | TIER_0 | Automated — LLM generates proposal (budget-gated) |
| review (assess proposal) | TIER_0 | Automated — reviewer_layer + meta_reviewer |
| accept | **TIER_1** | **Human required** — via ops API |
| implement (write code) | **TIER_1** | **Human required** — must review implementation |
| verify (run tests) | TIER_0 | Automated |
| promote (commit + deploy) | **TIER_1** | **Human required** |

**Hard rules:**
- Evolution proposals are NEVER auto-implemented
- Evolution changes to TIER_1 services require extra scrutiny on blast radius
- Evolution changes to TIER_2 files are auto-rejected at proposal stage

---

## 8. Reviewer & Meta-Reviewer Policy

| Agent | May | Must Not |
|-------|-----|----------|
| reviewer_layer | Assess any entity, produce verdicts, set auto_approvable flag | Override human rejection, approve TIER_2 changes, execute changes |
| meta_reviewer | Prioritize proposals, recommend strategic direction | Approve or reject individual changes, modify code, override reviewer |
| project_brain | Maintain codebase index, classify domains, refresh constitution | Modify its own constitution without TIER_1 approval, auto-execute actions |

**auto_approvable flag:** The reviewer may set `auto_approvable=true` only for changes that:
1. Touch exclusively TIER_0 domains
2. Have `risk_level=low`
3. Have `confidence=high`
4. Pass all constitution principle checks
5. Have blast radius ≤ 3 files

Even with `auto_approvable=true`, bugfix apply and promote stages still require human approval (§6).

---

## 9. Runtime Action Policy

These rules govern the orchestrator's operational actions (not code changes).

| Action | Tier | Auto-Execute | Conditions |
|--------|------|-------------|------------|
| webhook_repair | TIER_0 | Yes | Shop has missing/stale webhooks, repair_claim acquired |
| resolve_alert | TIER_0 | Yes | Alert exists and condition cleared |
| clear_redis_cache | TIER_0 | Yes | Cache key matches safe pattern |
| restart_worker | TIER_1 | No | Requires human approval via ops API |
| run_migration_dryrun | TIER_1 | No | Read-only schema check, requires approval |
| db_connection_reset | TIER_1 | No | Pool exhaustion detected, requires approval |
| restart_all_workers | TIER_2 | No | High blast radius — human only |

**Orchestrator mode constraints:**
- `deterministic` (current): Only TIER_0 actions auto-execute. TIER_1 logged as proposals.
- `proposal` (future): TIER_0 auto-execute + LLM proposes TIER_1. Nothing auto-approved.
- `hybrid` (future): TIER_0 auto-execute + LLM-approved TIER_0. TIER_1+ requires human. Requires confidence threshold.

**Safety limits:**
- Max 5 actions per orchestrator cycle
- 1-hour cooldown between identical actions on the same target
- All actions written to audit_log (append-only, never deleted)

---

## 10. Deploy Policy

**Who may deploy:** Human only. No agent may run `deploy.sh` or `pm2 restart` autonomously.

**Pre-deploy requirements (enforced by deploy.sh):**
1. pytest passes with 631+ tests
2. Next.js build succeeds
3. No uncommitted changes in working tree

**Post-deploy verification (enforced by deploy.sh):**
1. Backend and dashboard processes online
2. `/system/health` returns ok or degraded (not critical)
3. Database and Redis subsystems ok
4. Tracker endpoint responds 200
5. Session bootstrap returns 302
6. Ops diagnostic returns 200
7. Dashboard responds 200
8. Security headers present

**Rollback:** `deploy.sh --rollback` reverts to HEAD~1. Human-initiated only.

**Deploy exit codes:**
- 0 = success
- 1 = pre-deploy checks failed (nothing deployed)
- 2 = post-deploy checks failed (may need rollback)
- 3 = rollback executed

---

## 11. Cross-Cutting Rules

These apply to ALL tiers and ALL agents:

1. **Tenant isolation:** Every database query involving merchant data MUST be scoped by `shop_domain`. No cross-tenant data access, ever.
2. **Secrets:** Never log, commit, or include in diffs: API keys, tokens, passwords, `.env` values, encryption keys.
3. **Audit trail:** All agent actions that modify state must be logged to `audit_log`. Append-only — never update or delete audit records.
4. **LLM budget:** All LLM calls must go through `llm_budget.py`. Respect monthly cap (€5) and per-module daily limits. When budget exhausted, degrade gracefully — no retries, no bypass.
5. **Blocklist:** Skip `legacy.myshopify.com` in all automated processing.
6. **Idempotency:** Automated repair actions (webhook repair, cache clear) must be idempotent. Running twice must not cause harm.
7. **No speculative changes:** Agents fix what is broken or implement what is requested. No unsolicited refactoring, no "while I'm here" improvements.
8. **Error handling at boundaries:** Validate external input (Shopify webhooks, merchant requests, LLM responses). Trust internal code paths.
9. **Backward compatibility:** API response shapes consumed by the dashboard or tracker must not change without coordinated frontend updates.
