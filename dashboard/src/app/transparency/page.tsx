"use client";

/**
 * /transparency — public trust-signal page.
 *
 * The thing every SaaS competitor CAN'T publish. Every number on this
 * page is pulled from an append-only audit surface. If a value would
 * be embarrassing, the competitor's option is to not publish it; our
 * option is to fix the underlying system first, then publish. Same
 * design language as /status (amber #e8a04e eyebrows, one big number
 * per card, dark background).
 *
 * Paired with /public/transparency endpoint (60s cached).
 *
 * Copy is draft — founder review of headline/framing owed before
 * linking from landing. See SESSION_STATE.
 */

import { useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "https://api.hedgesparkhq.com";
const POLL_MS = 60_000;

type SelfHealing = {
  autonomous_fixes_7d: number;
  autonomous_fixes_30d: number;
  last_fix_at: string | null;
};

type LlmDrift = {
  status: "pending" | "measured";
  last_run_iso_week: string | null;
  json_parse_rate: number | null;
  refusal_rate: number | null;
  severity_valid_rate: number | null;
  provider: string | null;
  model: string | null;
};

type PiiGuard = {
  violations_7d: number;
  counter_available: boolean;
};

type AuditIntegrity = {
  chained_rows: number;
  legacy_rows: number;
  violations: number;
  head_matches_redis: boolean | null;
};

type Preflight = {
  audit_count: number;
  audit_names: string[];
};

type HoldoutProof = {
  actions_measured_30d: number;
  actions_success_30d: number;
  actions_no_effect_30d: number;
};

type Tests = {
  backend_tests_passing: number | null;
  last_preflight_at: string | null;
};

type TransparencyResponse = {
  self_healing: SelfHealing;
  llm_drift: LlmDrift;
  pii_guard: PiiGuard;
  audit_integrity: AuditIntegrity;
  preflight: Preflight;
  holdout_proof: HoldoutProof;
  tests: Tests;
  checked_at: string;
};

function relativeTime(iso: string | null): string {
  if (!iso) return "—";
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function pct(rate: number | null): string {
  if (rate === null || rate === undefined) return "—";
  return `${Math.round(rate * 100)}%`;
}

function Section({
  eyebrow,
  children,
}: {
  eyebrow: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-10">
      <h2 className="mb-3 text-[12px] font-bold uppercase tracking-[0.18em] text-[#e8a04e]">
        {eyebrow}
      </h2>
      {children}
    </section>
  );
}

function ReceiptCard({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-[#e8a04e]/25 bg-gradient-to-br from-[#e8a04e]/[0.06] via-transparent to-[#7c3aed]/[0.04] p-6">
      {children}
    </div>
  );
}

function BigNumber({
  value,
  label,
  hint,
  accent = false,
}: {
  value: string | number;
  label: string;
  hint?: string;
  accent?: boolean;
}) {
  return (
    <div>
      <div className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400">
        {label}
      </div>
      <div
        className={`mt-1 text-[28px] font-extrabold tabular-nums ${
          accent ? "text-[#e8a04e]" : "text-slate-100"
        }`}
      >
        {value}
      </div>
      {hint && <div className="text-[11px] text-slate-500">{hint}</div>}
    </div>
  );
}

export default function TransparencyPage() {
  const [data, setData] = useState<TransparencyResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      const r = await fetch(`${API_BASE}/public/transparency`, {
        cache: "no-store",
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j: TransparencyResponse = await r.json();
      setData(j);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, []);

  return (
    <main className="min-h-screen bg-[#0a0a0c] px-6 py-16 text-slate-100">
      <div className="mx-auto max-w-3xl">
        {/* Header */}
        <div className="mb-10">
          <a
            href="/"
            className="text-[12px] font-semibold uppercase tracking-[0.18em] text-[#e8a04e] hover:text-[#f0b56b]"
          >
            Hedge Spark
          </a>
          <h1 className="mt-2 text-[28px] font-extrabold tracking-tight text-white sm:text-[34px]">
            Trust, with receipts
          </h1>
          <p className="mt-2 text-[14px] leading-relaxed text-slate-400">
            Every number on this page is pulled live from an append-only
            surface inside our system. If it would be embarrassing, our
            answer is to fix the system first, then publish. Competitors
            publish uptime badges; this page is what we publish instead.
          </p>
        </div>

        {loading && !data && (
          <div className="space-y-3">
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <div
                key={i}
                className="h-24 animate-pulse rounded-2xl border border-white/[0.06] bg-white/[0.02]"
              />
            ))}
          </div>
        )}

        {error && !data && (
          <div className="rounded-xl border border-rose-400/20 bg-rose-500/[0.05] p-4 text-[13px] text-rose-300">
            {error}
          </div>
        )}

        {data && (
          <>
            {/* Self-healing receipts */}
            <Section eyebrow="Autonomous fixes — receipts">
              <ReceiptCard>
                <div className="grid gap-4 sm:grid-cols-3">
                  <BigNumber
                    label="Last 7 days"
                    value={data.self_healing.autonomous_fixes_7d}
                    hint="incidents auto-triaged or auto-fixed"
                    accent
                  />
                  <BigNumber
                    label="Last 30 days"
                    value={data.self_healing.autonomous_fixes_30d}
                    hint="incidents auto-triaged or auto-fixed"
                  />
                  <BigNumber
                    label="Latest fix"
                    value={relativeTime(data.self_healing.last_fix_at)}
                    hint="from the append-only audit log"
                  />
                </div>
                <p className="mt-5 text-[12px] leading-relaxed text-slate-400">
                  Every fix is an audit_log row with a hash-chain anchor.
                  Competitors publish a 99.9% uptime number; we publish
                  the actual log of what the self-healing pipeline did.
                </p>
              </ReceiptCard>
            </Section>

            {/* Holdout-measured outcomes */}
            <Section eyebrow="Holdout-measured outcomes">
              <ReceiptCard>
                <div className="grid gap-4 sm:grid-cols-3">
                  <BigNumber
                    label="Actions measured"
                    value={data.holdout_proof.actions_measured_30d}
                    hint="in the last 30 days"
                    accent
                  />
                  <BigNumber
                    label="Measured success"
                    value={data.holdout_proof.actions_success_30d}
                    hint="improved merchant metric"
                  />
                  <BigNumber
                    label="Measured no-effect"
                    value={data.holdout_proof.actions_no_effect_30d}
                    hint="tried, did not move the metric"
                  />
                </div>
                <p className="mt-5 text-[12px] leading-relaxed text-slate-400">
                  Every action the pipeline takes is paired with a
                  baseline snapshot and a post-action evaluation. We
                  never claim a win we cannot measure — and we publish
                  the no-effect count too, because honesty compounds.
                </p>
              </ReceiptCard>
            </Section>

            {/* LLM behavioral drift */}
            <Section eyebrow="LLM behavioral drift — weekly">
              <ReceiptCard>
                {data.llm_drift.status === "pending" ? (
                  <div className="text-[13px] leading-relaxed text-slate-400">
                    The weekly real-model drift corpus runs Sundays
                    06:00 UTC. Numbers appear here after the first run.
                    We check the primary LLM provider&apos;s JSON
                    validity, refusal rate, and severity-vocabulary
                    adherence on a fixed prompt set — so a quiet model
                    update cannot silently break production prompts.
                  </div>
                ) : (
                  <>
                    <div className="grid gap-4 sm:grid-cols-3">
                      <BigNumber
                        label="JSON parse"
                        value={pct(data.llm_drift.json_parse_rate)}
                        hint="strict-JSON emitter prompts"
                        accent
                      />
                      <BigNumber
                        label="Refusal rate"
                        value={pct(data.llm_drift.refusal_rate)}
                        hint="adversarial-prompt refusals"
                      />
                      <BigNumber
                        label="Severity vocab"
                        value={pct(data.llm_drift.severity_valid_rate)}
                        hint="P0/P1/P2 strict adherence"
                      />
                    </div>
                    <p className="mt-5 text-[12px] leading-relaxed text-slate-400">
                      Last run:{" "}
                      <span className="text-slate-300">
                        {data.llm_drift.last_run_iso_week}
                      </span>{" "}
                      on{" "}
                      <span className="text-slate-300">
                        {data.llm_drift.provider} {data.llm_drift.model}
                      </span>
                      . Rates are compared against an 8-week rolling
                      maximum — a drop larger than 15 points fires an
                      ops alert within the hour.
                    </p>
                  </>
                )}
              </ReceiptCard>
            </Section>

            {/* PII guard + audit chain */}
            <Section eyebrow="Data-path integrity">
              <div className="grid gap-3 sm:grid-cols-2">
                <ReceiptCard>
                  <BigNumber
                    label="PII blocks, last 7 days"
                    value={data.pii_guard.violations_7d}
                    hint={
                      data.pii_guard.violations_7d === 0
                        ? "no merchant PII reached any LLM call"
                        : "violations intercepted before upstream dispatch"
                    }
                    accent={data.pii_guard.violations_7d === 0}
                  />
                  <p className="mt-4 text-[11px] leading-relaxed text-slate-500">
                    Every LLM call is gated by a deterministic PII
                    scanner: emails, phone numbers, tokens, IBANs, card
                    shapes, password assignments. A blocked call never
                    leaves the server.
                  </p>
                </ReceiptCard>
                <ReceiptCard>
                  <BigNumber
                    label="Audit chain"
                    value={
                      data.audit_integrity.violations === 0
                        ? "Verified"
                        : `${data.audit_integrity.violations} violations`
                    }
                    hint={`${data.audit_integrity.chained_rows} chained rows checked`}
                    accent={data.audit_integrity.violations === 0}
                  />
                  <p className="mt-4 text-[11px] leading-relaxed text-slate-500">
                    Every audit_log row stores a hash of itself plus the
                    prior row. A tampered row breaks the chain and the
                    pipeline self-alerts. No silent deletion possible.
                  </p>
                </ReceiptCard>
              </div>
            </Section>

            {/* Preflight guards */}
            <Section eyebrow="Structural guards on every commit">
              <ReceiptCard>
                <div className="flex flex-wrap items-baseline gap-3">
                  <div className="text-[42px] font-extrabold tabular-nums text-[#e8a04e]">
                    {data.preflight.audit_count}
                  </div>
                  <div className="text-[14px] text-slate-300">
                    structural audits run before any commit is allowed
                  </div>
                </div>
                <p className="mt-4 text-[12px] leading-relaxed text-slate-400">
                  A preflight hook runs every audit below on every
                  commit. A single failure blocks the commit — including
                  mine, the ones the autonomous pipeline wants to
                  propose. Guards grew out of past incidents; every
                  name here is a bug that already happened once and
                  can no longer recur.
                </p>
                <div className="mt-5 flex flex-wrap gap-1.5">
                  {data.preflight.audit_names.map((n) => (
                    <span
                      key={n}
                      className="rounded-md border border-white/[0.08] bg-white/[0.02] px-2 py-1 text-[10px] font-semibold text-slate-400"
                    >
                      {n}
                    </span>
                  ))}
                </div>
              </ReceiptCard>
            </Section>

            {/* Footer note */}
            <p className="mt-12 text-[11px] leading-relaxed text-slate-500">
              Snapshot last checked {relativeTime(data.checked_at)}. Page
              refreshes every 60 seconds. Source endpoint:{" "}
              <code className="text-slate-400">/public/transparency</code>.
            </p>
          </>
        )}
      </div>
    </main>
  );
}
