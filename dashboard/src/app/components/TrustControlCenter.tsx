"use client";

/**
 * TrustControlCenter — Delegated Autonomy killer UX.
 *
 * THE feature that redefines the product:
 *   "I trust HedgeSpark to run price tests within -5%/+0% on these
 *    SKUs, max 3/day, pause if confidence < 80%, panic stop anytime."
 *
 * No competitor SMB product lets merchants grant autonomous execution
 * authority with bounded guardrails. This is the poster feature.
 *
 * Layout:
 *   [ BIG HEADER — 11/10 glow — Trust Control Center ]
 *   [ Impact Summary — revenue impact, contracts active, 30d record ]
 *   [ Contract Grid — one card per (action_type) with quota meters ]
 *   [ Grant New Trust — expandable form with sliders ]
 *   [ Recent Executions — audit log with outcomes ]
 *   [ PANIC STOP — always visible, bottom right ]
 *
 * Data: GET /pro/trust/summary + /pro/trust/executions
 */

import { useEffect, useState, useCallback } from "react";
import { apiClient } from "@/app/lib/api-client";
import { formatMoneyCompact } from "@/app/app/_lib/formatters";

type Contract = {
  id: number;
  shop_domain: string;
  action_type: string;
  max_per_day: number;
  max_per_week: number;
  discount_floor_pct: number;
  discount_ceiling_pct: number;
  confidence_threshold: number;
  auto_pause_on_drop_pct: number;
  require_holdout: boolean;
  scope_type: string;
  scope_values: string[] | null;
  status: string;
  created_at: string | null;
  revoked_at: string | null;
  revoked_reason: string | null;
  note: string | null;
};

type Execution = {
  id: number;
  contract_id: number;
  action_type: string;
  target_url: string | null;
  executed_at: string;
  confidence: number | null;
  discount_pct_applied: number | null;
  outcome: string | null;
  revenue_delta_eur: number | null;
  measured_at: string | null;
};

type Summary = {
  shop_domain: string;
  active_contracts: number;
  paused_contracts: number;
  executions_last_30d: number;
  revenue_impact_eur: number;
  effective_rate: number;
  contracts: Contract[];
  // Shop's native currency — revenue_impact_eur and execution
  // revenue_delta_eur are denominated in this currency.
  currency?: string;
};

const ACTION_META: Record<string, { label: string; icon: string; color: string; desc: string }> = {
  SCARCITY_NUDGE: {
    label: "Scarcity nudges",
    icon: "🔥",
    color: "#f97316",
    desc: "Inject 'only X left' messages on high-intent visitors.",
  },
  RETARGET_HOT_TRAFFIC: {
    label: "Return visitor retargeting",
    icon: "🎯",
    color: "#8b5cf6",
    desc: "Close the loop on warm visitors who didn't convert.",
  },
  PRICE_TEST: {
    label: "Price tests",
    icon: "💰",
    color: "#10b981",
    desc: "Run controlled price experiments within your bounds.",
  },
  FLASH_INCENTIVE: {
    label: "Flash incentives",
    icon: "⚡",
    color: "#eab308",
    desc: "Time-limited offers during live traffic spikes.",
  },
};

const fmtEur = (n: number, currency?: string): string =>
  formatMoneyCompact(n, currency || "USD");

const fmtPct = (n: number, decimals = 0): string => `${n.toFixed(decimals)}%`;

const fmtTime = (iso: string | null): string => {
  if (!iso) return "—";
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return d.toLocaleDateString();
};

export function TrustControlCenter({ apiBase, isProUser }: { apiBase: string; isProUser: boolean }) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [executions, setExecutions] = useState<Execution[]>([]);
  const [loading, setLoading] = useState(true);
  const [grantOpen, setGrantOpen] = useState(false);
  const [confirmPanic, setConfirmPanic] = useState(false);
  const [autopilotBusy, setAutopilotBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [summaryResp, execResp] = await Promise.all([
        apiClient.GET("/pro/trust/summary"),
        apiClient.GET("/pro/trust/executions", { params: { query: { limit: 20 } } }),
      ]);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      if (summaryResp.data) setSummary(summaryResp.data as any);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setExecutions(((execResp.data as any) || []) as Execution[]);
    } catch (err) {
      console.error("trust_control_center load failed", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isProUser) {
      setLoading(false);
      return;
    }
    load();
  }, [isProUser, load]);

  const autopilot = useCallback(
    async (mode: "conservative" | "balanced" | "aggressive") => {
      const warn =
        mode === "aggressive"
          ? "This grants HedgeSpark aggressive autonomy (up to -20% discounts, 10+ actions/day). Continue?"
          : mode === "balanced"
            ? "This grants HedgeSpark balanced autonomy (up to -10% discounts, 4+ actions/day). Continue?"
            : "This grants HedgeSpark conservative autonomy (tiny price tests only, 2 actions/day). Continue?";
      if (!confirm(warn)) return;
      setAutopilotBusy(mode);
      try {
        const { data, error: autoErr } = await apiClient.POST("/pro/trust/autopilot", {
          body: { mode },
        });
        if (autoErr || !data) {
          alert("Autopilot failed");
          return;
        }
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const contractsCreated = (data as any).contracts_created;
        alert(`Autopilot ${mode} activated — ${contractsCreated} contracts ready.`);
        await load();
      } catch (err) {
        console.error(err);
      } finally {
        setAutopilotBusy(null);
      }
    },
    [load],
  );

  const panic = useCallback(async () => {
    try {
      const { data } = await apiClient.POST("/pro/trust/panic");
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const revokedCount = (data as any)?.revoked_count ?? 0;
      alert(`Panic stop: ${revokedCount} contract(s) revoked.`);
      setConfirmPanic(false);
      await load();
    } catch (err) {
      console.error(err);
      alert("Panic stop failed — contact support.");
    }
  }, [load]);

  const revokeOne = useCallback(
    async (id: number) => {
      if (!confirm("Revoke this trust contract? The system will stop auto-executing this action.")) return;
      try {
        await apiClient.DELETE("/pro/trust/contracts/{contract_id}", {
          params: { path: { contract_id: id } },
        });
        await load();
      } catch (err) {
        console.error(err);
      }
    },
    [load],
  );

  if (!isProUser) {
    return (
      <div
        style={{
          padding: "32px",
          borderRadius: "16px",
          background: "linear-gradient(135deg, #0f172a 0%, #1e293b 100%)",
          border: "1px solid rgba(232,160,78,0.3)",
          textAlign: "center",
        }}
      >
        <div style={{ fontSize: "48px", marginBottom: "12px" }}>🔐</div>
        <h3 style={{ color: "#e8a04e", fontSize: "24px", marginBottom: "8px", fontWeight: 700 }}>
          Trust Control Center
        </h3>
        <p style={{ color: "#cbd5e1", fontSize: "15px", lineHeight: 1.6, maxWidth: "540px", margin: "0 auto" }}>
          Pro feature. Grant HedgeSpark the authority to auto-execute revenue-optimizing actions
          within the bounds you control. The first SMB intelligence that works while you sleep — safely.
        </p>
      </div>
    );
  }

  if (loading) {
    return (
      <div style={{ padding: "40px", textAlign: "center", color: "#94a3b8" }}>
        Loading trust control center…
      </div>
    );
  }

  const contracts = summary?.contracts || [];
  const active = contracts.filter((c) => c.status === "active");
  const allActionTypes = Object.keys(ACTION_META);
  const ungranted = allActionTypes.filter((t) => !active.some((c) => c.action_type === t));

  return (
    <div style={{ marginBottom: "32px" }}>
      {/* ────────────── HEADER ────────────── */}
      <div
        style={{
          padding: "28px 32px",
          borderRadius: "20px 20px 0 0",
          background: "linear-gradient(135deg, #0f172a 0%, #1e293b 100%)",
          borderBottom: "1px solid rgba(232,160,78,0.2)",
          position: "relative",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            position: "absolute",
            top: "-40%",
            right: "-10%",
            width: "400px",
            height: "400px",
            background: "radial-gradient(circle, rgba(232,160,78,0.08) 0%, transparent 70%)",
            pointerEvents: "none",
          }}
        />
        <div style={{ display: "flex", alignItems: "center", gap: "16px", position: "relative" }}>
          <div style={{ fontSize: "40px" }}>🛡️</div>
          <div style={{ flex: 1 }}>
            <h2
              style={{
                color: "#e8a04e",
                fontSize: "26px",
                fontWeight: 800,
                margin: 0,
                letterSpacing: "-0.02em",
              }}
            >
              Trust Control Center
            </h2>
            <p style={{ color: "#cbd5e1", fontSize: "14px", margin: "4px 0 0", maxWidth: "640px" }}>
              Grant HedgeSpark the authority to auto-execute revenue actions within your guardrails.
              You stay in control. The system acts on your terms — or not at all.
            </p>
          </div>
          <button
            onClick={() => setGrantOpen(true)}
            style={{
              padding: "12px 22px",
              borderRadius: "10px",
              background: "linear-gradient(135deg, #e8a04e 0%, #f59e0b 100%)",
              color: "#0f172a",
              fontWeight: 700,
              fontSize: "14px",
              border: "none",
              cursor: "pointer",
              boxShadow: "0 4px 14px rgba(232,160,78,0.35)",
            }}
          >
            + Grant New Trust
          </button>
        </div>
      </div>

      {/* ────────────── IMPACT SUMMARY ────────────── */}
      <div
        style={{
          padding: "24px 32px",
          background: "#0b1220",
          display: "grid",
          gridTemplateColumns: "repeat(4, 1fr)",
          gap: "24px",
          borderLeft: "1px solid rgba(148,163,184,0.1)",
          borderRight: "1px solid rgba(148,163,184,0.1)",
        }}
      >
        <StatPill
          label="Active contracts"
          value={String(summary?.active_contracts || 0)}
          sub={summary?.paused_contracts ? `${summary.paused_contracts} paused` : "all live"}
          color="#10b981"
        />
        <StatPill
          label="Autonomous actions (30d)"
          value={String(summary?.executions_last_30d || 0)}
          sub={summary?.effective_rate != null ? `${fmtPct(summary.effective_rate * 100)} effective` : "—"}
          color="#8b5cf6"
        />
        <StatPill
          label="Revenue impact (30d)"
          value={fmtEur(summary?.revenue_impact_eur || 0, summary?.currency)}
          sub="holdout-measured"
          color={summary && summary.revenue_impact_eur >= 0 ? "#10b981" : "#ef4444"}
        />
        <StatPill label="Your control" value="100%" sub="panic stop ready" color="#e8a04e" />
      </div>

      {/* ────────────── AUTOPILOT MODE — one-click grant ────────────── */}
      {active.length === 0 && (
        <div
          style={{
            padding: "20px 24px",
            background: "linear-gradient(135deg, rgba(232,160,78,0.08) 0%, rgba(245,158,11,0.04) 100%)",
            borderLeft: "1px solid rgba(232,160,78,0.25)",
            borderRight: "1px solid rgba(232,160,78,0.25)",
            borderBottom: "1px solid rgba(148,163,184,0.08)",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "10px",
              marginBottom: "6px",
            }}
          >
            <span style={{ fontSize: "18px" }}>✨</span>
            <div
              style={{
                color: "#e8a04e",
                fontSize: "11px",
                fontWeight: 700,
                letterSpacing: "0.08em",
                textTransform: "uppercase",
              }}
            >
              Autopilot — one-click setup
            </div>
          </div>
          <div
            style={{
              color: "#cbd5e1",
              fontSize: "13px",
              marginBottom: "14px",
              maxWidth: "600px",
              lineHeight: 1.5,
            }}
          >
            Pick how much you want HedgeSpark to do while you sleep. Each option creates
            several pre-tuned safety contracts at once. You can tune them later, and the
            panic button is always one click away.
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
              gap: "12px",
            }}
          >
            {[
              {
                mode: "conservative" as const,
                title: "Conservative",
                emoji: "🐢",
                bullets: ["Up to 2 nudges/day", "Tiny price tests (-3% max)", "90% confidence required"],
                color: "#10b981",
              },
              {
                mode: "balanced" as const,
                title: "Balanced",
                emoji: "⚖️",
                bullets: ["Up to 4 nudges/day", "Price tests (-7%)", "Flash offers (-10%)", "80% confidence"],
                color: "#e8a04e",
                recommended: true,
              },
              {
                mode: "aggressive" as const,
                title: "Aggressive",
                emoji: "🚀",
                bullets: ["Up to 10 nudges/day", "Deep price tests (-15%)", "Flash offers (-20%)", "72% confidence"],
                color: "#f43f5e",
              },
            ].map((opt) => (
              <button
                key={opt.mode}
                onClick={() => autopilot(opt.mode)}
                disabled={autopilotBusy !== null}
                style={{
                  position: "relative",
                  padding: "16px 14px",
                  borderRadius: "14px",
                  background: "rgba(15,23,42,0.6)",
                  border: `1px solid ${opt.color}${opt.recommended ? "66" : "33"}`,
                  color: "#e2e8f0",
                  textAlign: "left",
                  cursor: autopilotBusy ? "wait" : "pointer",
                  transition: "transform 0.2s ease, border-color 0.2s ease",
                  opacity: autopilotBusy && autopilotBusy !== opt.mode ? 0.5 : 1,
                }}
                onMouseEnter={(e) => {
                  if (!autopilotBusy) e.currentTarget.style.transform = "translateY(-2px)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.transform = "translateY(0)";
                }}
              >
                {opt.recommended && (
                  <span
                    style={{
                      position: "absolute",
                      top: "-9px",
                      right: "12px",
                      padding: "2px 8px",
                      borderRadius: "6px",
                      background: "#e8a04e",
                      color: "#0f172a",
                      fontSize: "9px",
                      fontWeight: 800,
                      textTransform: "uppercase",
                      letterSpacing: "0.06em",
                    }}
                  >
                    Recommended
                  </span>
                )}
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "8px",
                    marginBottom: "10px",
                  }}
                >
                  <span style={{ fontSize: "22px" }}>{opt.emoji}</span>
                  <span style={{ fontSize: "15px", fontWeight: 700, color: opt.color }}>
                    {opt.title}
                  </span>
                </div>
                <ul
                  style={{
                    margin: 0,
                    paddingLeft: "16px",
                    fontSize: "12px",
                    lineHeight: 1.55,
                    color: "#94a3b8",
                  }}
                >
                  {opt.bullets.map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
                {autopilotBusy === opt.mode && (
                  <div
                    style={{
                      marginTop: "10px",
                      fontSize: "11px",
                      color: opt.color,
                      fontWeight: 700,
                    }}
                  >
                    Activating…
                  </div>
                )}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* ────────────── CONTRACT CARDS ────────────── */}
      <div
        style={{
          padding: "24px 32px",
          background: "#0b1220",
          borderLeft: "1px solid rgba(148,163,184,0.1)",
          borderRight: "1px solid rgba(148,163,184,0.1)",
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
          gap: "16px",
        }}
      >
        {active.length === 0 && (
          <div
            style={{
              gridColumn: "1 / -1",
              textAlign: "center",
              padding: "48px 16px",
              color: "#94a3b8",
              fontSize: "15px",
              borderRadius: "12px",
              border: "1px dashed rgba(232,160,78,0.3)",
              background: "rgba(232,160,78,0.03)",
            }}
          >
            <div style={{ fontSize: "48px", marginBottom: "12px" }}>✨</div>
            <div style={{ fontWeight: 700, color: "#e2e8f0", fontSize: "18px", marginBottom: "6px" }}>
              No active trust contracts yet
            </div>
            <div>
              Grant HedgeSpark authority on one action type to unlock autonomous revenue optimization.
              <br />
              Every contract has quotas, confidence gates, and a panic button.
            </div>
          </div>
        )}
        {active.map((c) => (
          <ContractCard key={c.id} contract={c} onRevoke={() => revokeOne(c.id)} />
        ))}
      </div>

      {/* ────────────── RECENT EXECUTIONS ────────────── */}
      {executions.length > 0 && (
        <div
          style={{
            padding: "24px 32px",
            background: "#0b1220",
            borderLeft: "1px solid rgba(148,163,184,0.1)",
            borderRight: "1px solid rgba(148,163,184,0.1)",
          }}
        >
          <h3 style={{ color: "#e2e8f0", fontSize: "16px", fontWeight: 700, margin: "0 0 16px" }}>
            Recent autonomous actions
          </h3>
          <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
            {executions.slice(0, 8).map((x) => {
              const meta = ACTION_META[x.action_type] || { label: x.action_type, icon: "•", color: "#94a3b8", desc: "" };
              const outcomeBadge = (() => {
                if (x.outcome === "effective") return { bg: "rgba(16,185,129,0.15)", color: "#10b981", text: "effective" };
                if (x.outcome === "ineffective") return { bg: "rgba(239,68,68,0.15)", color: "#ef4444", text: "ineffective" };
                if (x.outcome === "inconclusive") return { bg: "rgba(148,163,184,0.15)", color: "#94a3b8", text: "inconclusive" };
                return { bg: "rgba(232,160,78,0.15)", color: "#e8a04e", text: "measuring…" };
              })();
              return (
                <div
                  key={x.id}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "auto 1fr auto auto",
                    alignItems: "center",
                    gap: "14px",
                    padding: "10px 14px",
                    background: "rgba(15,23,42,0.5)",
                    borderRadius: "8px",
                    border: "1px solid rgba(148,163,184,0.08)",
                  }}
                >
                  <div style={{ fontSize: "20px" }}>{meta.icon}</div>
                  <div>
                    <div style={{ color: "#e2e8f0", fontSize: "14px", fontWeight: 600 }}>
                      {meta.label}
                      {x.target_url && (
                        <span style={{ color: "#94a3b8", fontWeight: 400 }}> · {x.target_url.slice(0, 40)}</span>
                      )}
                    </div>
                    <div style={{ color: "#94a3b8", fontSize: "12px", marginTop: "2px" }}>
                      {fmtTime(x.executed_at)}
                      {x.confidence != null && ` · ${fmtPct(x.confidence * 100)} confidence`}
                    </div>
                  </div>
                  <div
                    style={{
                      color: x.revenue_delta_eur != null && x.revenue_delta_eur > 0 ? "#10b981" : "#94a3b8",
                      fontSize: "14px",
                      fontWeight: 700,
                      minWidth: "60px",
                      textAlign: "right",
                    }}
                  >
                    {x.revenue_delta_eur != null ? fmtEur(x.revenue_delta_eur, summary?.currency) : "—"}
                  </div>
                  <span
                    style={{
                      padding: "4px 10px",
                      borderRadius: "6px",
                      background: outcomeBadge.bg,
                      color: outcomeBadge.color,
                      fontSize: "11px",
                      fontWeight: 700,
                      textTransform: "uppercase",
                      letterSpacing: "0.04em",
                    }}
                  >
                    {outcomeBadge.text}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ────────────── PANIC STOP ────────────── */}
      <div
        style={{
          padding: "20px 32px",
          background: "linear-gradient(180deg, #0b1220 0%, #7f1d1d 120%)",
          borderRadius: "0 0 20px 20px",
          border: "1px solid rgba(239,68,68,0.3)",
          borderTop: "none",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "16px",
        }}
      >
        <div>
          <div style={{ color: "#fca5a5", fontSize: "13px", fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase" }}>
            Emergency stop
          </div>
          <div style={{ color: "#fee2e2", fontSize: "14px", marginTop: "4px" }}>
            Revoke every active contract instantly. Nothing auto-executes until you grant again.
          </div>
        </div>
        {!confirmPanic ? (
          <button
            onClick={() => setConfirmPanic(true)}
            disabled={active.length === 0}
            style={{
              padding: "12px 24px",
              borderRadius: "10px",
              background: active.length === 0 ? "rgba(127,29,29,0.3)" : "linear-gradient(135deg, #dc2626 0%, #991b1b 100%)",
              color: "#fff",
              fontWeight: 700,
              fontSize: "14px",
              border: "1px solid rgba(239,68,68,0.5)",
              cursor: active.length === 0 ? "not-allowed" : "pointer",
              opacity: active.length === 0 ? 0.4 : 1,
            }}
          >
            🛑 Panic Stop
          </button>
        ) : (
          <div style={{ display: "flex", gap: "8px" }}>
            <button
              onClick={panic}
              style={{
                padding: "12px 20px",
                borderRadius: "10px",
                background: "#dc2626",
                color: "#fff",
                fontWeight: 700,
                border: "none",
                cursor: "pointer",
              }}
            >
              Confirm revoke all
            </button>
            <button
              onClick={() => setConfirmPanic(false)}
              style={{
                padding: "12px 20px",
                borderRadius: "10px",
                background: "transparent",
                color: "#fca5a5",
                border: "1px solid rgba(239,68,68,0.5)",
                cursor: "pointer",
              }}
            >
              Cancel
            </button>
          </div>
        )}
      </div>

      {/* ────────────── GRANT MODAL ────────────── */}
      {grantOpen && (
        <GrantTrustModal
          apiBase={apiBase}
          availableTypes={ungranted.length > 0 ? ungranted : allActionTypes}
          onClose={() => setGrantOpen(false)}
          onGranted={() => {
            setGrantOpen(false);
            load();
          }}
        />
      )}
    </div>
  );
}

// ============================================================================
// StatPill
// ============================================================================

function StatPill({ label, value, sub, color }: { label: string; value: string; sub: string; color: string }) {
  return (
    <div
      style={{
        padding: "14px 18px",
        background: "rgba(15,23,42,0.5)",
        borderRadius: "12px",
        border: "1px solid rgba(148,163,184,0.1)",
      }}
    >
      <div style={{ color: "#94a3b8", fontSize: "11px", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em" }}>
        {label}
      </div>
      <div style={{ color, fontSize: "26px", fontWeight: 800, marginTop: "6px", fontVariantNumeric: "tabular-nums" }}>
        {value}
      </div>
      <div style={{ color: "#94a3b8", fontSize: "12px", marginTop: "4px" }}>{sub}</div>
    </div>
  );
}

// ============================================================================
// ContractCard — one card per active contract
// ============================================================================

function ContractCard({ contract, onRevoke }: { contract: Contract; onRevoke: () => void }) {
  const meta = ACTION_META[contract.action_type] || { label: contract.action_type, icon: "•", color: "#94a3b8", desc: "" };

  return (
    <div
      style={{
        padding: "20px",
        borderRadius: "14px",
        background: "linear-gradient(135deg, rgba(15,23,42,0.8) 0%, rgba(30,41,59,0.6) 100%)",
        border: `1px solid ${meta.color}33`,
        position: "relative",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          position: "absolute",
          top: "-50px",
          right: "-50px",
          width: "150px",
          height: "150px",
          background: `radial-gradient(circle, ${meta.color}15 0%, transparent 70%)`,
          pointerEvents: "none",
        }}
      />
      <div style={{ display: "flex", alignItems: "center", gap: "12px", marginBottom: "14px" }}>
        <div style={{ fontSize: "24px" }}>{meta.icon}</div>
        <div style={{ flex: 1 }}>
          <div style={{ color: "#e2e8f0", fontSize: "15px", fontWeight: 700 }}>{meta.label}</div>
          <div style={{ color: "#94a3b8", fontSize: "11px" }}>{meta.desc}</div>
        </div>
        <span
          style={{
            padding: "3px 8px",
            borderRadius: "6px",
            background: "rgba(16,185,129,0.15)",
            color: "#10b981",
            fontSize: "10px",
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "0.05em",
          }}
        >
          active
        </span>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "10px",
          fontSize: "12px",
          color: "#cbd5e1",
          marginBottom: "14px",
        }}
      >
        <RowStat label="Max/day" value={String(contract.max_per_day)} />
        <RowStat label="Max/week" value={String(contract.max_per_week)} />
        <RowStat
          label="Discount range"
          value={`${contract.discount_floor_pct >= 0 ? "+" : ""}${contract.discount_floor_pct}% to ${
            contract.discount_ceiling_pct >= 0 ? "+" : ""
          }${contract.discount_ceiling_pct}%`}
        />
        <RowStat label="Min confidence" value={`${Math.round(contract.confidence_threshold * 100)}%`} />
        <RowStat label="Auto-pause on drop" value={`-${contract.auto_pause_on_drop_pct}%`} />
        <RowStat label="Holdout" value={contract.require_holdout ? "required" : "optional"} />
      </div>

      <div style={{ display: "flex", gap: "8px" }}>
        <button
          onClick={onRevoke}
          style={{
            flex: 1,
            padding: "8px 12px",
            borderRadius: "8px",
            background: "transparent",
            border: "1px solid rgba(239,68,68,0.4)",
            color: "#fca5a5",
            fontSize: "12px",
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          Revoke
        </button>
      </div>
    </div>
  );
}

function RowStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={{ color: "#94a3b8", fontSize: "10px", textTransform: "uppercase", letterSpacing: "0.04em" }}>
        {label}
      </div>
      <div style={{ color: "#e2e8f0", fontWeight: 600, fontSize: "13px" }}>{value}</div>
    </div>
  );
}

// ============================================================================
// GrantTrustModal — sliders form
// ============================================================================

function GrantTrustModal({
  apiBase,
  availableTypes,
  onClose,
  onGranted,
}: {
  apiBase: string;
  availableTypes: string[];
  onClose: () => void;
  onGranted: () => void;
}) {
  const [actionType, setActionType] = useState(availableTypes[0] || "PRICE_TEST");
  const [maxPerDay, setMaxPerDay] = useState(3);
  const [maxPerWeek, setMaxPerWeek] = useState(10);
  const [discountFloor, setDiscountFloor] = useState(-5);
  const [discountCeiling, setDiscountCeiling] = useState(0);
  const [confidence, setConfidence] = useState(0.8);
  const [autoPauseDrop, setAutoPauseDrop] = useState(15);
  const [requireHoldout, setRequireHoldout] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = useCallback(async () => {
    setSubmitting(true);
    setErr(null);
    try {
      const { error: postErr } = await apiClient.POST("/pro/trust/contracts", {
        body: {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          action_type: actionType as any,
          max_per_day: maxPerDay,
          max_per_week: maxPerWeek,
          discount_floor_pct: discountFloor,
          discount_ceiling_pct: discountCeiling,
          confidence_threshold: confidence,
          auto_pause_on_drop_pct: autoPauseDrop,
          require_holdout: requireHoldout,
          scope_type: "all",
        },
      });
      if (postErr) throw new Error("grant failed");
      onGranted();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "failed";
      setErr(msg);
    } finally {
      setSubmitting(false);
    }
  }, [actionType, maxPerDay, maxPerWeek, discountFloor, discountCeiling, confidence, autoPauseDrop, requireHoldout, onGranted]);

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        background: "rgba(0,0,0,0.7)",
        backdropFilter: "blur(8px)",
        zIndex: 1000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "24px",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "linear-gradient(135deg, #0f172a 0%, #1e293b 100%)",
          borderRadius: "20px",
          padding: "32px",
          maxWidth: "540px",
          width: "100%",
          maxHeight: "90vh",
          overflowY: "auto",
          border: "1px solid rgba(232,160,78,0.3)",
          boxShadow: "0 24px 64px rgba(0,0,0,0.6)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "20px" }}>
          <div>
            <h3 style={{ color: "#e8a04e", fontSize: "20px", fontWeight: 800, margin: 0 }}>
              Grant Trust
            </h3>
            <p style={{ color: "#94a3b8", fontSize: "13px", margin: "4px 0 0" }}>
              Set the guardrails. HedgeSpark executes only within these bounds.
            </p>
          </div>
          <button
            onClick={onClose}
            style={{
              background: "transparent",
              border: "none",
              color: "#94a3b8",
              fontSize: "20px",
              cursor: "pointer",
            }}
          >
            ✕
          </button>
        </div>

        {/* Action type */}
        <Field label="Action type">
          <select
            value={actionType}
            onChange={(e) => setActionType(e.target.value)}
            style={{
              width: "100%",
              padding: "10px 12px",
              borderRadius: "8px",
              background: "rgba(15,23,42,0.6)",
              border: "1px solid rgba(148,163,184,0.2)",
              color: "#e2e8f0",
              fontSize: "14px",
            }}
          >
            {availableTypes.map((t) => {
              const m = ACTION_META[t] || { label: t, icon: "•" };
              return (
                <option key={t} value={t}>
                  {m.icon} {m.label}
                </option>
              );
            })}
          </select>
        </Field>

        <Field label={`Max per day: ${maxPerDay}`}>
          <input
            type="range"
            min={0}
            max={20}
            value={maxPerDay}
            onChange={(e) => setMaxPerDay(Number(e.target.value))}
            style={{ width: "100%" }}
          />
        </Field>

        <Field label={`Max per week: ${maxPerWeek}`}>
          <input
            type="range"
            min={0}
            max={100}
            value={maxPerWeek}
            onChange={(e) => setMaxPerWeek(Math.max(maxPerDay, Number(e.target.value)))}
            style={{ width: "100%" }}
          />
        </Field>

        <Field label={`Discount range: ${discountFloor}% to ${discountCeiling}%`}>
          <div style={{ display: "flex", gap: "12px" }}>
            <div style={{ flex: 1 }}>
              <div style={{ color: "#94a3b8", fontSize: "11px", marginBottom: "4px" }}>Floor (most aggressive cut)</div>
              <input
                type="range"
                min={-30}
                max={0}
                value={discountFloor}
                onChange={(e) => setDiscountFloor(Number(e.target.value))}
                style={{ width: "100%" }}
              />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ color: "#94a3b8", fontSize: "11px", marginBottom: "4px" }}>Ceiling (max markup)</div>
              <input
                type="range"
                min={-30}
                max={30}
                value={discountCeiling}
                onChange={(e) => setDiscountCeiling(Math.max(discountFloor, Number(e.target.value)))}
                style={{ width: "100%" }}
              />
            </div>
          </div>
        </Field>

        <Field label={`Min confidence to act: ${Math.round(confidence * 100)}%`}>
          <input
            type="range"
            min={50}
            max={99}
            value={Math.round(confidence * 100)}
            onChange={(e) => setConfidence(Number(e.target.value) / 100)}
            style={{ width: "100%" }}
          />
        </Field>

        <Field label={`Auto-pause if revenue drops >${autoPauseDrop}%`}>
          <input
            type="range"
            min={5}
            max={50}
            value={autoPauseDrop}
            onChange={(e) => setAutoPauseDrop(Number(e.target.value))}
            style={{ width: "100%" }}
          />
        </Field>

        <Field label="">
          <label style={{ display: "flex", alignItems: "center", gap: "10px", cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={requireHoldout}
              onChange={(e) => setRequireHoldout(e.target.checked)}
              style={{ width: "18px", height: "18px", cursor: "pointer" }}
            />
            <span style={{ color: "#e2e8f0", fontSize: "14px" }}>
              Require holdout measurement <span style={{ color: "#94a3b8" }}>(recommended for proof)</span>
            </span>
          </label>
        </Field>

        {err && (
          <div
            style={{
              padding: "10px 14px",
              borderRadius: "8px",
              background: "rgba(239,68,68,0.15)",
              border: "1px solid rgba(239,68,68,0.4)",
              color: "#fca5a5",
              fontSize: "13px",
              marginBottom: "16px",
            }}
          >
            {err}
          </div>
        )}

        <div style={{ display: "flex", gap: "10px", marginTop: "20px" }}>
          <button
            onClick={submit}
            disabled={submitting}
            style={{
              flex: 1,
              padding: "12px 20px",
              borderRadius: "10px",
              background: "linear-gradient(135deg, #e8a04e 0%, #f59e0b 100%)",
              color: "#0f172a",
              fontWeight: 700,
              fontSize: "14px",
              border: "none",
              cursor: submitting ? "wait" : "pointer",
              opacity: submitting ? 0.6 : 1,
            }}
          >
            {submitting ? "Granting…" : "Grant Trust"}
          </button>
          <button
            onClick={onClose}
            style={{
              padding: "12px 20px",
              borderRadius: "10px",
              background: "transparent",
              color: "#cbd5e1",
              border: "1px solid rgba(148,163,184,0.3)",
              cursor: "pointer",
            }}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: "16px" }}>
      {label && (
        <div style={{ color: "#e2e8f0", fontSize: "13px", fontWeight: 600, marginBottom: "8px" }}>{label}</div>
      )}
      {children}
    </div>
  );
}
