"use client";

/**
 * RuleBuilderCard (ζ2-UI) — Low-code rule builder, merchant-facing.
 *
 * Merchant defines:
 *   IF [trigger] AND [condition] AND [condition] THEN [action]
 *
 * UI flow:
 *   1. List of existing rules with status toggle + fire count
 *   2. "+ Create rule" → inline form with dropdowns
 *   3. Test button → dry-run against a sample payload
 *
 * Brand-consistent (amber #e8a04e, dark gradient, rounded). Copy
 * idiot-proof. No jargon — just "when X, do Y".
 *
 * Data: GET/POST/PATCH/DELETE /pro/rules, GET /pro/rules/catalog
 */

import { useCallback, useEffect, useState } from "react";

type TriggerOption = { id: string; label: string };
type ActionOption = { id: string; label: string };

type Catalog = {
  triggers: TriggerOption[];
  actions: ActionOption[];
  ops: string[];
};

type Condition = {
  field: string;
  op: string;
  value: string | number;
};

type Rule = {
  id: number;
  shop_domain: string;
  name: string;
  trigger_signal: string;
  conditions: Condition[];
  action: { type: string; [k: string]: unknown };
  status: string;
  max_per_hour: number;
  fired_count: number;
  last_fired_at: string | null;
  created_at: string | null;
};

const OP_LABEL: Record<string, string> = {
  eq: "equals",
  ne: "not equals",
  gt: "greater than",
  lt: "less than",
  gte: "≥",
  lte: "≤",
  contains: "contains",
  in: "in list",
  regex: "matches pattern",
};

const ACTION_ICON: Record<string, string> = {
  send_klaviyo_event: "📧",
  notify_slack: "💬",
  create_nudge: "⚡",
  write_note: "📝",
  emit_ops_alert: "🚨",
};

const STATUS_COLOR: Record<string, string> = {
  active: "#10b981",
  draft: "#94a3b8",
  paused: "#e8a04e",
  disabled: "#f43f5e",
};

export function RuleBuilderCard({ apiBase, isProUser }: { apiBase: string; isProUser: boolean }) {
  const [rules, setRules] = useState<Rule[]>([]);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    try {
      const [rulesResp, catalogResp] = await Promise.all([
        fetch(`${apiBase}/pro/rules`, { credentials: "include" }).then((r) => r.json()),
        fetch(`${apiBase}/pro/rules/catalog`, { credentials: "include" }).then((r) => r.json()),
      ]);
      setRules(rulesResp);
      setCatalog(catalogResp);
    } catch (err) {
      console.error("rule_builder load failed", err);
    } finally {
      setLoading(false);
    }
  }, [apiBase]);

  useEffect(() => {
    if (!isProUser) {
      setLoading(false);
      return;
    }
    load();
  }, [isProUser, load]);

  const toggleStatus = useCallback(
    async (rule: Rule) => {
      const next = rule.status === "active" ? "paused" : "active";
      try {
        await fetch(`${apiBase}/pro/rules/${rule.id}`, {
          method: "PATCH",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: next }),
        });
        load();
      } catch (err) {
        console.error(err);
      }
    },
    [apiBase, load],
  );

  const deleteRule = useCallback(
    async (rule: Rule) => {
      if (!confirm(`Delete rule "${rule.name}"?`)) return;
      try {
        await fetch(`${apiBase}/pro/rules/${rule.id}`, {
          method: "DELETE",
          credentials: "include",
        });
        load();
      } catch (err) {
        console.error(err);
      }
    },
    [apiBase, load],
  );

  if (!isProUser) return null;
  if (loading || !catalog) return null;

  return (
    <div
      style={{
        marginBottom: "24px",
        padding: "24px 28px",
        borderRadius: "18px",
        background: "linear-gradient(135deg, #0b1220 0%, #141d33 100%)",
        border: "1px solid rgba(232,160,78,0.25)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "12px",
          marginBottom: "6px",
        }}
      >
        <span style={{ fontSize: "22px" }}>⚙️</span>
        <div style={{ flex: 1 }}>
          <h3
            style={{
              color: "#e8a04e",
              fontSize: "18px",
              fontWeight: 800,
              margin: 0,
            }}
          >
            Your automation rules
          </h3>
          <div style={{ color: "#94a3b8", fontSize: "12px", marginTop: "2px" }}>
            When something happens in your store, tell HedgeSpark what to do.
          </div>
        </div>
        <button
          onClick={() => setCreating(true)}
          style={{
            padding: "8px 16px",
            borderRadius: "8px",
            background: "linear-gradient(135deg, #e8a04e 0%, #f59e0b 100%)",
            color: "#0f172a",
            fontWeight: 700,
            fontSize: "12px",
            border: "none",
            cursor: "pointer",
          }}
        >
          + New rule
        </button>
      </div>

      {rules.length === 0 && !creating && (
        <div
          style={{
            padding: "24px 16px",
            marginTop: "16px",
            textAlign: "center",
            border: "1px dashed rgba(232,160,78,0.3)",
            borderRadius: "12px",
            background: "rgba(232,160,78,0.03)",
          }}
        >
          <div style={{ fontSize: "28px", marginBottom: "6px" }}>✨</div>
          <div style={{ color: "#e2e8f0", fontSize: "14px", fontWeight: 600 }}>
            No rules yet
          </div>
          <div style={{ color: "#94a3b8", fontSize: "12px", marginTop: "4px" }}>
            Try: "When a customer abandons their cart, send a Klaviyo event"
          </div>
        </div>
      )}

      {rules.length > 0 && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "8px",
            marginTop: "14px",
          }}
        >
          {rules.map((rule) => {
            const triggerLabel =
              catalog.triggers.find((t) => t.id === rule.trigger_signal)?.label ||
              rule.trigger_signal;
            const actionIcon = ACTION_ICON[rule.action.type] || "•";
            return (
              <div
                key={rule.id}
                style={{
                  padding: "12px 14px",
                  borderRadius: "10px",
                  background: "rgba(15,23,42,0.5)",
                  border: "1px solid rgba(148,163,184,0.08)",
                  display: "grid",
                  gridTemplateColumns: "auto 1fr auto auto auto",
                  alignItems: "center",
                  gap: "12px",
                }}
              >
                <div style={{ fontSize: "20px" }}>{actionIcon}</div>
                <div>
                  <div style={{ color: "#e2e8f0", fontSize: "14px", fontWeight: 600 }}>
                    {rule.name}
                  </div>
                  <div style={{ color: "#64748b", fontSize: "11px", marginTop: "2px" }}>
                    When: {triggerLabel} ·{" "}
                    {rule.conditions.length > 0
                      ? `${rule.conditions.length} condition${rule.conditions.length > 1 ? "s" : ""}`
                      : "no conditions"}{" "}
                    · Fired {rule.fired_count}×
                  </div>
                </div>
                <span
                  style={{
                    padding: "3px 8px",
                    borderRadius: "6px",
                    background: `${STATUS_COLOR[rule.status]}20`,
                    color: STATUS_COLOR[rule.status],
                    fontSize: "10px",
                    fontWeight: 700,
                    textTransform: "uppercase",
                    letterSpacing: "0.05em",
                  }}
                >
                  {rule.status}
                </span>
                <button
                  onClick={() => toggleStatus(rule)}
                  style={{
                    padding: "6px 12px",
                    borderRadius: "6px",
                    background: "transparent",
                    border: "1px solid rgba(148,163,184,0.25)",
                    color: "#cbd5e1",
                    fontSize: "11px",
                    fontWeight: 600,
                    cursor: "pointer",
                  }}
                >
                  {rule.status === "active" ? "Pause" : "Activate"}
                </button>
                <button
                  onClick={() => deleteRule(rule)}
                  aria-label="delete"
                  style={{
                    padding: "6px 10px",
                    borderRadius: "6px",
                    background: "transparent",
                    border: "1px solid rgba(244,63,94,0.3)",
                    color: "#fca5a5",
                    fontSize: "11px",
                    cursor: "pointer",
                  }}
                >
                  ✕
                </button>
              </div>
            );
          })}
        </div>
      )}

      {creating && (
        <RuleForm
          apiBase={apiBase}
          catalog={catalog}
          onCancel={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            load();
          }}
        />
      )}
    </div>
  );
}

// ============================================================================
// RuleForm — inline creation form
// ============================================================================

function RuleForm({
  apiBase,
  catalog,
  onCancel,
  onCreated,
}: {
  apiBase: string;
  catalog: Catalog;
  onCancel: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [trigger, setTrigger] = useState(catalog.triggers[0]?.id || "");
  const [conditions, setConditions] = useState<Condition[]>([]);
  const [actionType, setActionType] = useState(catalog.actions[0]?.id || "");
  const [actionParam, setActionParam] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const addCondition = () =>
    setConditions([...conditions, { field: "source", op: "eq", value: "" }]);
  const updateCondition = (i: number, patch: Partial<Condition>) => {
    const next = [...conditions];
    next[i] = { ...next[i], ...patch };
    setConditions(next);
  };
  const removeCondition = (i: number) =>
    setConditions(conditions.filter((_, j) => j !== i));

  const buildAction = (): Record<string, unknown> => {
    const base = { type: actionType };
    if (actionType === "send_klaviyo_event") return { ...base, event_name: actionParam || "custom_signal" };
    if (actionType === "notify_slack") return { ...base, event_type: "goal_at_risk" };
    if (actionType === "create_nudge")
      return { ...base, nudge_type: "SCARCITY_NUDGE", product_url: actionParam };
    if (actionType === "write_note") return { ...base, body: actionParam || "Rule triggered" };
    if (actionType === "emit_ops_alert")
      return { ...base, alert_type: "rule_triggered", severity: "info" };
    return base;
  };

  const submit = async () => {
    setErr(null);
    if (!name.trim()) {
      setErr("Name is required");
      return;
    }
    setSubmitting(true);
    try {
      const resp = await fetch(`${apiBase}/pro/rules`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          trigger_signal: trigger,
          conditions,
          action: buildAction(),
          status: "active",
          max_per_hour: 30,
        }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        setErr(typeof body.detail === "string" ? body.detail : "Failed to create");
        return;
      }
      onCreated();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      style={{
        marginTop: "16px",
        padding: "18px 20px",
        borderRadius: "14px",
        background: "rgba(15,23,42,0.7)",
        border: "1px solid rgba(232,160,78,0.35)",
      }}
    >
      <div style={{ marginBottom: "14px" }}>
        <label
          style={{
            display: "block",
            color: "#94a3b8",
            fontSize: "11px",
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "0.06em",
            marginBottom: "6px",
          }}
        >
          Rule name
        </label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Watch cart abandoners from Google"
          style={{
            width: "100%",
            padding: "10px 12px",
            borderRadius: "8px",
            background: "rgba(11,18,32,0.8)",
            border: "1px solid rgba(148,163,184,0.2)",
            color: "#e2e8f0",
            fontSize: "14px",
            outline: "none",
          }}
        />
      </div>

      <div style={{ display: "flex", gap: "12px", marginBottom: "14px", flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 280px" }}>
          <label
            style={{
              display: "block",
              color: "#94a3b8",
              fontSize: "11px",
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              marginBottom: "6px",
            }}
          >
            When this happens
          </label>
          <select
            value={trigger}
            onChange={(e) => setTrigger(e.target.value)}
            style={{
              width: "100%",
              padding: "10px 12px",
              borderRadius: "8px",
              background: "rgba(11,18,32,0.8)",
              border: "1px solid rgba(148,163,184,0.2)",
              color: "#e2e8f0",
              fontSize: "14px",
            }}
          >
            {catalog.triggers.map((t) => (
              <option key={t.id} value={t.id}>
                {t.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Conditions */}
      <div style={{ marginBottom: "14px" }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: "6px",
          }}
        >
          <label
            style={{
              color: "#94a3b8",
              fontSize: "11px",
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.06em",
            }}
          >
            And these conditions match (optional)
          </label>
          <button
            onClick={addCondition}
            style={{
              background: "transparent",
              border: "1px solid rgba(148,163,184,0.25)",
              color: "#cbd5e1",
              fontSize: "11px",
              padding: "4px 10px",
              borderRadius: "6px",
              cursor: "pointer",
            }}
          >
            + Add condition
          </button>
        </div>
        {conditions.map((c, i) => (
          <div
            key={i}
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 110px 1fr auto",
              gap: "8px",
              marginBottom: "8px",
            }}
          >
            <input
              type="text"
              value={c.field}
              onChange={(e) => updateCondition(i, { field: e.target.value })}
              placeholder="field (e.g. source)"
              style={{
                padding: "8px 10px",
                borderRadius: "6px",
                background: "rgba(11,18,32,0.8)",
                border: "1px solid rgba(148,163,184,0.15)",
                color: "#e2e8f0",
                fontSize: "13px",
              }}
            />
            <select
              value={c.op}
              onChange={(e) => updateCondition(i, { op: e.target.value })}
              style={{
                padding: "8px 10px",
                borderRadius: "6px",
                background: "rgba(11,18,32,0.8)",
                border: "1px solid rgba(148,163,184,0.15)",
                color: "#e2e8f0",
                fontSize: "12px",
              }}
            >
              {catalog.ops.map((o) => (
                <option key={o} value={o}>
                  {OP_LABEL[o] || o}
                </option>
              ))}
            </select>
            <input
              type="text"
              value={c.value as string}
              onChange={(e) => updateCondition(i, { value: e.target.value })}
              placeholder="value"
              style={{
                padding: "8px 10px",
                borderRadius: "6px",
                background: "rgba(11,18,32,0.8)",
                border: "1px solid rgba(148,163,184,0.15)",
                color: "#e2e8f0",
                fontSize: "13px",
              }}
            />
            <button
              onClick={() => removeCondition(i)}
              style={{
                padding: "6px 10px",
                background: "transparent",
                border: "1px solid rgba(244,63,94,0.3)",
                color: "#fca5a5",
                borderRadius: "6px",
                cursor: "pointer",
                fontSize: "12px",
              }}
            >
              ✕
            </button>
          </div>
        ))}
      </div>

      {/* Action */}
      <div style={{ display: "flex", gap: "12px", marginBottom: "14px", flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 240px" }}>
          <label
            style={{
              display: "block",
              color: "#94a3b8",
              fontSize: "11px",
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              marginBottom: "6px",
            }}
          >
            Then do this
          </label>
          <select
            value={actionType}
            onChange={(e) => setActionType(e.target.value)}
            style={{
              width: "100%",
              padding: "10px 12px",
              borderRadius: "8px",
              background: "rgba(11,18,32,0.8)",
              border: "1px solid rgba(148,163,184,0.2)",
              color: "#e2e8f0",
              fontSize: "14px",
            }}
          >
            {catalog.actions.map((a) => (
              <option key={a.id} value={a.id}>
                {ACTION_ICON[a.id] || "•"} {a.label}
              </option>
            ))}
          </select>
        </div>
        <div style={{ flex: "1 1 240px" }}>
          <label
            style={{
              display: "block",
              color: "#94a3b8",
              fontSize: "11px",
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              marginBottom: "6px",
            }}
          >
            Detail
          </label>
          <input
            type="text"
            value={actionParam}
            onChange={(e) => setActionParam(e.target.value)}
            placeholder={
              actionType === "send_klaviyo_event"
                ? "Event name (e.g. cart_watch)"
                : actionType === "write_note"
                  ? "Note body"
                  : actionType === "create_nudge"
                    ? "Product URL (e.g. /products/x)"
                    : "(no detail needed)"
            }
            style={{
              width: "100%",
              padding: "10px 12px",
              borderRadius: "8px",
              background: "rgba(11,18,32,0.8)",
              border: "1px solid rgba(148,163,184,0.2)",
              color: "#e2e8f0",
              fontSize: "14px",
            }}
          />
        </div>
      </div>

      {err && (
        <div
          style={{
            marginBottom: "12px",
            padding: "10px 14px",
            borderRadius: "8px",
            background: "rgba(244,63,94,0.1)",
            border: "1px solid rgba(244,63,94,0.3)",
            color: "#fca5a5",
            fontSize: "13px",
          }}
        >
          {err}
        </div>
      )}

      <div style={{ display: "flex", gap: "10px" }}>
        <button
          onClick={submit}
          disabled={submitting}
          style={{
            flex: 1,
            padding: "11px 20px",
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
          {submitting ? "Creating…" : "Create rule"}
        </button>
        <button
          onClick={onCancel}
          style={{
            padding: "11px 18px",
            borderRadius: "10px",
            background: "transparent",
            border: "1px solid rgba(148,163,184,0.3)",
            color: "#cbd5e1",
            cursor: "pointer",
          }}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
