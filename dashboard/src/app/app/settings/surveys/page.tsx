"use client";

/**
 * /app/settings/surveys — Post-purchase survey configuration (Gap #7).
 *
 * Lite: read-only preset + Upgrade CTA.
 * Pro:  editable question, options list (3-8), allow-other toggle,
 *       show-on-order-status toggle.
 *
 * Reads + writes:
 *   GET /survey/config?shop=<domain>     (public; same endpoint the
 *                                         extension fetches at render
 *                                         time — single source of truth)
 *   PUT /merchant/survey/config          (Lite + Pro per 0-60 parity)
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import { FloorLayout } from "../../../components/FloorLayout";
import { apiClient } from "../../../lib/api-client";
import type { SessionState } from "../../../lib/useSession";

const API_BASE =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_BASE_URL) ||
  "";

const QUESTION_MAX = 160;
const OPTION_LABEL_MAX = 24;
const OPTION_VALUE_MAX = 32;
const MIN_OPTIONS = 3;
const MAX_OPTIONS = 8;

type Option = { label: string; value: string };

type Config = {
  question: string;
  options: Option[];
  allow_other: boolean;
  show_on_order_status: boolean;
};

const DEFAULT_CONFIG: Config = {
  question: "How did you hear about us?",
  options: [
    { label: "Instagram", value: "instagram" },
    { label: "TikTok", value: "tiktok" },
    { label: "Google", value: "google" },
    { label: "Friend", value: "friend" },
    { label: "Email", value: "email" },
  ],
  allow_other: true,
  show_on_order_status: true,
};

export default function SurveysSettingsPage() {
  return (
    <FloorLayout floor="settings">
      {(session) => <SurveysSurface session={session} />}
    </FloorLayout>
  );
}

function SurveysSurface({ session }: { session: SessionState }) {
  return (
    <>
      <div className="mb-8">
        <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px] font-semibold text-slate-400">
          <Link href="/app" className="text-slate-400 hover:text-[#e8a04e]">
            ← Dashboard
          </Link>
          <span className="text-slate-600">/</span>
          <Link href="/app/settings" className="text-slate-400 hover:text-[#e8a04e]">
            Settings
          </Link>
          <span className="text-slate-600">/</span>
          <span className="text-slate-300">Post-purchase survey</span>
        </div>
        <h1 className="text-[28px] font-extrabold tracking-tight text-[#e8a04e] sm:text-[32px]">
          Post-purchase survey
        </h1>
        <p className="mt-2 max-w-2xl text-[13.5px] leading-relaxed text-slate-400">
          A single warm question on the Thank-You and Order-Status pages —
          how customers found you. Edit the question, swap the answer
          choices, toggle the free-text fallback.
        </p>
      </div>

      <ProForm shop={session.shop} />
    </>
  );
}

// ---------------------------------------------------------------------------
// Editable form — Lite + Pro per `feedback_0_60_parity_doctrine.md`.
// Every $0-60 competitor (KnoCommerce/Fairing/Zigpoll/Pathlight) ships
// customizable survey questions in their entry tier; HedgeSpark Lite
// (€39) is in the same band → full customization here.
// ---------------------------------------------------------------------------

function ProForm({ shop }: { shop: string | null }) {
  const [config, setConfig] = useState<Config>(DEFAULT_CONFIG);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: "ok" | "err"; text: string } | null>(null);

  useEffect(() => {
    if (!shop || !API_BASE) return;
    fetch(`${API_BASE}/survey/config?shop=${encodeURIComponent(shop)}`, {
      credentials: "include",
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d) return;
        setConfig({
          question: d.question || DEFAULT_CONFIG.question,
          options: Array.isArray(d.options) && d.options.length ? d.options : DEFAULT_CONFIG.options,
          allow_other: typeof d.allow_other === "boolean" ? d.allow_other : true,
          show_on_order_status: !d.disabled_on_order_status,
        });
      })
      .catch(() => { /* fall back to defaults */ })
      .finally(() => setLoading(false));
  }, [shop]);

  async function save() {
    setMessage(null);
    setSaving(true);
    try {
      // Validate locally before sending
      if (!config.question.trim()) {
        setMessage({ type: "err", text: "Question cannot be empty." });
        setSaving(false);
        return;
      }
      if (config.options.length < MIN_OPTIONS || config.options.length > MAX_OPTIONS) {
        setMessage({
          type: "err",
          text: `You need ${MIN_OPTIONS}-${MAX_OPTIONS} answer choices.`,
        });
        setSaving(false);
        return;
      }
      const { error } = await apiClient.PUT("/merchant/survey/config", {
        body: {
          survey_question: config.question.trim(),
          survey_options: config.options,
          survey_allow_other: config.allow_other,
          survey_show_on_order_status: config.show_on_order_status,
        },
      });
      if (error) {
        const detail = (error as { detail?: string } | null)?.detail || "Save failed.";
        throw new Error(detail);
      }
      setMessage({ type: "ok", text: "Saved. Customers will see the new survey on the next order." });
    } catch (err) {
      const e = err as { message?: string };
      setMessage({ type: "err", text: e?.message || "Save failed. Try again?" });
    } finally {
      setSaving(false);
    }
  }

  function updateOption(i: number, patch: Partial<Option>) {
    setConfig((c) => ({
      ...c,
      options: c.options.map((o, idx) => (idx === i ? { ...o, ...patch } : o)),
    }));
  }

  function addOption() {
    if (config.options.length >= MAX_OPTIONS) return;
    setConfig((c) => ({
      ...c,
      options: [...c.options, { label: "", value: "" }],
    }));
  }

  function removeOption(i: number) {
    if (config.options.length <= MIN_OPTIONS) return;
    setConfig((c) => ({
      ...c,
      options: c.options.filter((_, idx) => idx !== i),
    }));
  }

  if (loading) {
    return (
      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
        <div className="h-3 w-44 animate-pulse rounded bg-white/[0.06]" />
        <div className="mt-3 h-20 animate-pulse rounded bg-white/[0.04]" />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {/* Question */}
      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
        <label className="block text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
          Question
        </label>
        <textarea
          value={config.question}
          onChange={(e) => setConfig((c) => ({ ...c, question: e.target.value.slice(0, QUESTION_MAX) }))}
          maxLength={QUESTION_MAX}
          rows={2}
          className="mt-2 w-full resize-none rounded-lg border border-white/[0.08] bg-white/[0.03] px-4 py-2.5 text-[14px] text-slate-200 placeholder:text-slate-600 focus:border-[#e8a04e]/60 focus:outline-none"
        />
        <div className="mt-1 text-right text-[10.5px] text-slate-400">
          {config.question.length} / {QUESTION_MAX}
        </div>
      </div>

      {/* Options */}
      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
        <div className="flex items-center justify-between">
          <label className="block text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">
            Answer choices ({config.options.length} / {MAX_OPTIONS})
          </label>
          <button
            onClick={addOption}
            disabled={config.options.length >= MAX_OPTIONS}
            className="rounded-lg border border-white/[0.1] bg-white/[0.04] px-3 py-1 text-[11px] font-semibold text-slate-300 transition hover:border-[#e8a04e]/40 hover:text-[#e8a04e] disabled:cursor-not-allowed disabled:opacity-40"
          >
            + Add choice
          </button>
        </div>
        <div className="mt-3 space-y-2">
          {config.options.map((opt, i) => (
            <div key={i} className="flex items-center gap-2">
              <input
                type="text"
                value={opt.label}
                onChange={(e) => updateOption(i, { label: e.target.value.slice(0, OPTION_LABEL_MAX) })}
                placeholder="Display label"
                className="flex-1 rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-[13px] text-slate-200 placeholder:text-slate-600 focus:border-[#e8a04e]/60 focus:outline-none"
              />
              <input
                type="text"
                value={opt.value}
                onChange={(e) => updateOption(i, { value: e.target.value.slice(0, OPTION_VALUE_MAX).toLowerCase() })}
                placeholder="value-key"
                className="w-32 rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 font-mono text-[12px] text-slate-300 placeholder:text-slate-600 focus:border-[#e8a04e]/60 focus:outline-none"
              />
              <button
                onClick={() => removeOption(i)}
                disabled={config.options.length <= MIN_OPTIONS}
                aria-label={`Remove ${opt.label || "option"}`}
                className="flex-shrink-0 rounded-lg border border-rose-400/20 bg-rose-500/[0.05] px-2 py-2 text-rose-300 transition hover:border-rose-400/40 hover:bg-rose-500/[0.10] disabled:cursor-not-allowed disabled:opacity-30"
              >
                <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          ))}
        </div>
        <p className="mt-3 text-[11px] text-slate-400">
          Label = what customers see. Value-key = the slug stored in your
          analytics (lowercase, no spaces — keep it short and stable).
        </p>
      </div>

      {/* Toggles */}
      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-6">
        <ToggleRow
          label="Allow free-text “Other” answer"
          help="Adds an Other option that reveals a 1-line text input. Free text is automatically PII-scrubbed before storage."
          checked={config.allow_other}
          onChange={(v) => setConfig((c) => ({ ...c, allow_other: v }))}
        />
        <div className="mt-4 border-t border-white/[0.05] pt-4">
          <ToggleRow
            label="Show on Order Status page"
            help="If a customer revisits their order page later, the survey is shown again unless they already answered. Disable to limit display to the first Thank-You view only."
            checked={config.show_on_order_status}
            onChange={(v) => setConfig((c) => ({ ...c, show_on_order_status: v }))}
          />
        </div>
      </div>

      {/* Save */}
      <div className="flex items-center justify-end gap-3">
        {message && (
          <span
            className={`text-[12px] ${
              message.type === "ok" ? "text-emerald-300" : "text-rose-300"
            }`}
          >
            {message.text}
          </span>
        )}
        <button
          onClick={save}
          disabled={saving}
          className="rounded-lg bg-emerald-500/90 px-5 py-2.5 text-[13px] font-bold uppercase tracking-[0.1em] text-white transition-colors hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {saving ? "Saving…" : "Save changes"}
        </button>
      </div>
    </div>
  );
}

function ToggleRow({
  label,
  help,
  checked,
  onChange,
}: {
  label: string;
  help: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="min-w-0 flex-1">
        <div className="text-[13px] font-semibold text-slate-200">{label}</div>
        <p className="mt-0.5 text-[11.5px] leading-relaxed text-slate-400">{help}</p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={`mt-1 flex h-5 w-9 flex-shrink-0 items-center rounded-full transition ${
          checked ? "bg-emerald-500/60" : "bg-white/[0.08]"
        }`}
      >
        <span
          className={`inline-block h-4 w-4 transform rounded-full transition ${
            checked ? "translate-x-4 bg-emerald-200" : "translate-x-0.5 bg-slate-500"
          }`}
        />
      </button>
    </div>
  );
}
