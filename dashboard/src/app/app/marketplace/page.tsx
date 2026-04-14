"use client";

/**
 * /app/marketplace — Phase Ω⁵ community marketplace UI.
 *
 * Merchants share nudge + rule templates that worked for them. Others
 * clone in one click. Proof-of-Work badges show real clone/upvote counts
 * so the list ranks by validated outcomes, not vanity.
 *
 * API: /pro/marketplace/templates  (list / clone / upvote)
 */

import { useCallback, useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";

type Template = {
  id: number;
  template_type: "nudge" | "rule";
  title: string;
  description: string | null;
  author_label: string;
  vertical: string;
  payload: Record<string, unknown>;
  upvotes: number;
  clone_count: number;
  created_at: string | null;
};

type Sort = "popular" | "recent" | "upvotes";
type Kind = "all" | "nudge" | "rule";

function relTime(iso: string | null): string {
  if (!iso) return "";
  try {
    const diffMs = Date.now() - new Date(iso).getTime();
    const days = Math.floor(diffMs / 86400000);
    if (days < 1) return "today";
    if (days === 1) return "yesterday";
    if (days < 30) return `${days}d ago`;
    if (days < 365) return `${Math.floor(days / 30)}mo ago`;
    return `${Math.floor(days / 365)}y ago`;
  } catch { return ""; }
}

function proofBand(clones: number, upvotes: number): { label: string; color: string } | null {
  const total = clones + upvotes;
  if (total >= 100) return { label: "proven", color: "#34d399" };
  if (total >= 25) return { label: "validated", color: "#a3e635" };
  if (total >= 5) return { label: "emerging", color: "#fbbf24" };
  return null;
}

export default function MarketplacePage() {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sort, setSort] = useState<Sort>("popular");
  const [kind, setKind] = useState<Kind>("all");
  const [query, setQuery] = useState("");
  const [cloning, setCloning] = useState<number | null>(null);
  const [cloned, setCloned] = useState<Record<number, boolean>>({});
  const [upvoted, setUpvoted] = useState<Record<number, boolean>>({});
  const [toast, setToast] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { data: j, error: err } = await apiClient.GET(
        "/pro/marketplace/templates",
        {
          params: {
            query: {
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              sort: sort as any,
              limit: 60,
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              ...(kind !== "all" ? { template_type: kind as any } : {}),
            },
          },
        },
      );
      if (err || !j) throw new Error("failed");
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setTemplates(((j as any).templates || []) as Template[]);
    } catch {
      setError("Could not load marketplace. Make sure you're signed in with a Pro session.");
    } finally {
      setLoading(false);
    }
  }, [sort, kind]);

  useEffect(() => { void load(); }, [load]);

  const filtered = templates.filter((t) => {
    if (!query.trim()) return true;
    const q = query.toLowerCase();
    return (
      t.title.toLowerCase().includes(q) ||
      (t.description || "").toLowerCase().includes(q) ||
      t.vertical.toLowerCase().includes(q)
    );
  });

  const clone = async (id: number) => {
    if (cloning != null || cloned[id]) return;
    setCloning(id);
    try {
      const { error: err } = await apiClient.POST(
        "/pro/marketplace/templates/{template_id}/clone",
        { params: { path: { template_id: id } } },
      );
      if (err) throw new Error();
      setCloned((c) => ({ ...c, [id]: true }));
      setToast("Cloned to your account — open it from the nudge list.");
      setTimeout(() => setToast(null), 4000);
    } catch {
      setToast("Clone failed. Try again in a moment.");
      setTimeout(() => setToast(null), 4000);
    } finally {
      setCloning(null);
    }
  };

  const upvote = async (id: number) => {
    if (upvoted[id]) return;
    setUpvoted((u) => ({ ...u, [id]: true }));
    // Optimistic
    setTemplates((ts) => ts.map((t) => (t.id === id ? { ...t, upvotes: t.upvotes + 1 } : t)));
    try {
      await apiClient.POST(
        "/pro/marketplace/templates/{template_id}/upvote",
        { params: { path: { template_id: id } } },
      );
    } catch {}
  };

  return (
    <div className="min-h-screen bg-[#0a0e1a] text-slate-100">
      <div className="mx-auto max-w-6xl px-6 py-10">
        {/* Header */}
        <div className="mb-8">
          <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.18em] text-[#d4893a]">
            Community · Phase Ω⁵
          </div>
          <h1 className="text-[28px] font-extrabold leading-tight text-white">
            Marketplace — templates from real merchants
          </h1>
          <p className="mt-2 max-w-2xl text-[14px] leading-relaxed text-slate-400">
            Nudge and rule templates published by the HedgeSpark network.
            Clone the ones your peers are already running. Every card shows a
            <span className="text-emerald-300"> proof-of-work</span> badge built
            from real clone and upvote counts — no vanity numbers.
          </p>
        </div>

        {/* Controls */}
        <div className="mb-6 flex flex-wrap items-center gap-3">
          <input
            type="text"
            placeholder="Search title, vertical…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="w-full max-w-xs rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-[13px] text-slate-100 placeholder:text-slate-500 focus:border-amber-400/40 focus:outline-none"
            aria-label="Search templates"
          />

          <div className="flex items-center gap-1 rounded-lg border border-white/10 bg-white/[0.02] p-1">
            {(["all", "nudge", "rule"] as Kind[]).map((k) => (
              <button
                key={k}
                onClick={() => setKind(k)}
                className={`rounded-md px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wide transition-colors ${
                  kind === k ? "bg-amber-500/15 text-amber-300" : "text-slate-400 hover:text-slate-200"
                }`}
              >
                {k}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-1 rounded-lg border border-white/10 bg-white/[0.02] p-1">
            {(["popular", "recent", "upvotes"] as Sort[]).map((s) => (
              <button
                key={s}
                onClick={() => setSort(s)}
                className={`rounded-md px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wide transition-colors ${
                  sort === s ? "bg-amber-500/15 text-amber-300" : "text-slate-400 hover:text-slate-200"
                }`}
              >
                {s}
              </button>
            ))}
          </div>

          <div className="ml-auto text-[11px] text-slate-500">
            {filtered.length} template{filtered.length === 1 ? "" : "s"}
          </div>
        </div>

        {/* Error */}
        {error && (
          <div className="mb-4 rounded-xl border border-rose-400/20 bg-rose-500/[0.06] p-4 text-[13px] text-rose-200">
            {error}
          </div>
        )}

        {/* Grid */}
        {loading ? (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {[...Array(6)].map((_, i) => (
              <div key={i} className="animate-pulse rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5">
                <div className="h-3 w-24 rounded bg-white/[0.06]" />
                <div className="mt-3 h-5 w-3/4 rounded bg-white/[0.06]" />
                <div className="mt-2 h-3 w-full rounded bg-white/[0.04]" />
                <div className="mt-2 h-3 w-2/3 rounded bg-white/[0.04]" />
              </div>
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-10 text-center">
            <div className="mb-2 text-[14px] font-semibold text-slate-200">No templates match your filters.</div>
            <p className="text-[12px] text-slate-500">Try a different sort, kind, or search term.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {filtered.map((t) => {
              const proof = proofBand(t.clone_count, t.upvotes);
              return (
                <article
                  key={t.id}
                  className="group flex flex-col rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5 transition-colors hover:border-amber-400/30"
                >
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <span
                      className="rounded-md px-2 py-0.5 text-[9px] font-bold uppercase tracking-wide"
                      style={{
                        color: t.template_type === "nudge" ? "#a5b4fc" : "#fcd34d",
                        background: t.template_type === "nudge" ? "rgba(165,180,252,0.1)" : "rgba(252,211,77,0.1)",
                        border: `1px solid ${t.template_type === "nudge" ? "rgba(165,180,252,0.3)" : "rgba(252,211,77,0.3)"}`,
                      }}
                    >
                      {t.template_type}
                    </span>
                    {proof && (
                      <span
                        className="rounded-full px-2 py-0.5 text-[9px] font-bold uppercase tracking-wide"
                        style={{ color: proof.color, background: proof.color + "15", border: `1px solid ${proof.color}40` }}
                        title={`${t.clone_count} clones · ${t.upvotes} upvotes`}
                      >
                        ✓ {proof.label}
                      </span>
                    )}
                  </div>

                  <h3 className="text-[15px] font-bold leading-snug text-white">{t.title}</h3>
                  {t.description && (
                    <p className="mt-1.5 line-clamp-3 text-[12px] leading-relaxed text-slate-400">{t.description}</p>
                  )}

                  <div className="mt-3 flex items-center gap-3 text-[10px] text-slate-500">
                    <span>by {t.author_label}</span>
                    <span>·</span>
                    <span>{t.vertical}</span>
                    <span>·</span>
                    <span>{relTime(t.created_at)}</span>
                  </div>

                  <div className="mt-4 flex items-center gap-2 border-t border-white/[0.05] pt-3">
                    <button
                      onClick={() => clone(t.id)}
                      disabled={cloned[t.id] || cloning === t.id}
                      className="flex-1 rounded-lg border border-amber-400/30 bg-amber-500/[0.08] px-3 py-2 text-[11px] font-bold uppercase tracking-wide text-amber-300 transition-colors hover:bg-amber-500/[0.15] disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {cloned[t.id] ? "✓ Cloned" : cloning === t.id ? "Cloning…" : "Clone"}
                    </button>
                    <button
                      onClick={() => upvote(t.id)}
                      disabled={upvoted[t.id]}
                      className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-[11px] font-semibold text-slate-300 transition-colors hover:bg-white/[0.06] disabled:opacity-60"
                      aria-label="Upvote"
                      title="Upvote"
                    >
                      ▲ <span className="tabular-nums">{t.upvotes}</span>
                    </button>
                    <span
                      className="inline-flex items-center gap-1 rounded-lg border border-white/5 bg-white/[0.02] px-2.5 py-2 text-[10px] text-slate-500"
                      title="Clone count"
                    >
                      ⎘ <span className="tabular-nums">{t.clone_count}</span>
                    </span>
                  </div>
                </article>
              );
            })}
          </div>
        )}

        {/* Toast */}
        {toast && (
          <div
            role="status"
            className="fixed bottom-6 right-6 z-50 rounded-xl border border-emerald-400/30 bg-emerald-500/[0.1] px-4 py-3 text-[12px] font-semibold text-emerald-200 shadow-lg backdrop-blur"
          >
            {toast}
          </div>
        )}
      </div>
    </div>
  );
}
