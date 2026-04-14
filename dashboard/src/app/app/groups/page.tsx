"use client";

/**
 * /app/groups — Multi-store consolidated dashboard.
 *
 * Lists merchant groups owned by the current shop's contact_email,
 * lets the founder create new groups, add member shops, and view a
 * cross-shop revenue rollup with per-shop breakdown.
 *
 * Real data from /pro/groups APIs.
 */

import { useCallback, useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";

type GroupMember = { shop_domain: string; label: string | null; is_primary: boolean };
type Group = {
  id: number;
  name: string;
  description: string | null;
  base_currency: string;
  members: GroupMember[];
};
type GroupsResponse = { groups: Group[] };

type DashboardMember = {
  shop_domain: string;
  label: string | null;
  is_primary: boolean;
  revenue_eur: number;
  orders: number;
  aov_eur: number;
};
type DashboardResponse = {
  group_id: number;
  name: string;
  base_currency: string;
  lookback_days: number;
  members: DashboardMember[];
  totals: { revenue_eur: number; orders: number; aov_eur: number };
  top_shop: DashboardMember | null;
  generated_at: string;
};

function fmtMoney(n: number, currency = "EUR"): string {
  if (n === 0) return currency === "EUR" ? "€0" : `${currency}0`;
  const a = Math.abs(n);
  const sym = currency === "EUR" ? "€" : currency + " ";
  if (a >= 1000) return sym + (a / 1000).toFixed(a >= 10_000 ? 0 : 1) + "k";
  return sym + Math.round(a);
}

export default function GroupsPage() {
  const [groups, setGroups] = useState<Group[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [dashboard, setDashboard] = useState<DashboardResponse | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [addingShop, setAddingShop] = useState(false);
  const [newShopDomain, setNewShopDomain] = useState("");
  const [newShopLabel, setNewShopLabel] = useState("");

  const loadGroups = useCallback(async () => {
    try {
      const { data: j, error: err } = await apiClient.GET("/pro/groups");
      if (err || !j) throw new Error("failed");
      const resp = j as unknown as GroupsResponse;
      setGroups(resp.groups || []);
      setSelectedId((cur) => cur ?? (resp.groups?.[0]?.id ?? null));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadDashboard = useCallback(async (id: number) => {
    try {
      const { data: j, error: err } = await apiClient.GET(
        "/pro/groups/{group_id}/dashboard",
        { params: { path: { group_id: id } } },
      );
      if (err || !j) throw new Error("failed");
      setDashboard(j as unknown as DashboardResponse);
    } catch {
      setDashboard(null);
    }
  }, []);

  useEffect(() => { loadGroups(); }, [loadGroups]);
  useEffect(() => { if (selectedId) loadDashboard(selectedId); }, [selectedId, loadDashboard]);

  const createGroup = async () => {
    if (!newName.trim()) return;
    setCreating(true);
    try {
      const { error: err } = await apiClient.POST("/pro/groups", {
        body: { name: newName.trim(), base_currency: "EUR" },
      });
      if (!err) {
        setNewName("");
        await loadGroups();
      }
    } finally {
      setCreating(false);
    }
  };

  const addMember = async () => {
    if (!selectedId || !newShopDomain.trim()) return;
    setAddingShop(true);
    try {
      const { error: err } = await apiClient.POST(
        "/pro/groups/{group_id}/members",
        {
          params: { path: { group_id: selectedId } },
          body: {
            shop_domain: newShopDomain.trim(),
            label: newShopLabel.trim() || null,
            is_primary: false,
          },
        },
      );
      if (!err) {
        setNewShopDomain("");
        setNewShopLabel("");
        await loadGroups();
        await loadDashboard(selectedId);
      }
    } finally {
      setAddingShop(false);
    }
  };

  return (
    <main className="min-h-screen bg-[#0a0a0c] px-6 py-10 text-slate-100" aria-label="Multi-store consolidated dashboard">
      <div className="mx-auto max-w-6xl">
        <header className="mb-8">
          <a
            href="/app"
            className="text-[12px] font-semibold uppercase tracking-[0.18em] text-[#e8a04e] hover:text-[#f0b56b]"
          >
            ← Back to dashboard
          </a>
          <h1 className="mt-2 text-[28px] font-extrabold tracking-tight text-white sm:text-[34px]">
            Multi-Store Brand View
          </h1>
          <p className="mt-1 text-[14px] text-slate-400">
            Run multiple Shopify stores under one brand? See them as one.
          </p>
        </header>

        {loading && (
          <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-8 text-center text-[13px] text-slate-500">
            Loading…
          </div>
        )}
        {error && (
          <div className="rounded-2xl border border-rose-400/20 bg-rose-500/[0.05] p-4 text-[13px] text-rose-300">
            {error}
          </div>
        )}

        {!loading && (
          <>
            <section className="mb-8 rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-[14px] font-bold text-white">Your groups</h2>
                <span className="text-[11px] text-slate-500">{groups.length} group{groups.length === 1 ? "" : "s"}</span>
              </div>

              {groups.length === 0 ? (
                <div className="rounded-xl border border-dashed border-white/[0.08] bg-white/[0.01] p-4 text-center text-[12px] text-slate-500">
                  No groups yet. Create one to consolidate stores.
                </div>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {groups.map((g) => (
                    <button
                      key={g.id}
                      onClick={() => setSelectedId(g.id)}
                      className={`rounded-full border px-3.5 py-1.5 text-[12px] font-semibold transition-all ${
                        selectedId === g.id
                          ? "border-[#d4893a]/40 bg-[#d4893a]/15 text-[#d4893a]"
                          : "border-white/[0.08] bg-white/[0.03] text-slate-300 hover:border-white/[0.18]"
                      }`}
                    >
                      {g.name}{" "}
                      <span className="ml-1 text-[10px] opacity-60">{g.members.length}</span>
                    </button>
                  ))}
                </div>
              )}

              <div className="mt-4 flex gap-2">
                <input
                  type="text"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="New group name"
                  aria-label="New group name"
                  className="min-w-0 flex-1 rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-[12px] text-slate-100 placeholder-slate-500 outline-none focus:border-[#d4893a]/40"
                />
                <button
                  onClick={createGroup}
                  disabled={creating || !newName.trim()}
                  className="rounded-lg border border-[#d4893a]/30 bg-[#d4893a]/15 px-4 py-2 text-[12px] font-bold text-[#d4893a] hover:bg-[#d4893a]/25 disabled:opacity-50"
                >
                  {creating ? "…" : "Create"}
                </button>
              </div>
            </section>

            {dashboard && (
              <>
                <section className="mb-6 rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
                  <h2 className="text-[14px] font-bold text-white">{dashboard.name} — {dashboard.lookback_days}d totals</h2>
                  <div className="mt-3 grid grid-cols-3 gap-3">
                    <div className="rounded-xl border border-emerald-400/20 bg-emerald-500/[0.06] p-4 text-center">
                      <div className="text-[10px] uppercase tracking-wide text-emerald-400">Revenue</div>
                      <div className="mt-1 text-[22px] font-extrabold text-emerald-300">
                        {fmtMoney(dashboard.totals.revenue_eur, dashboard.base_currency)}
                      </div>
                    </div>
                    <div className="rounded-xl border border-white/[0.08] bg-white/[0.03] p-4 text-center">
                      <div className="text-[10px] uppercase tracking-wide text-slate-500">Orders</div>
                      <div className="mt-1 text-[22px] font-extrabold text-white">
                        {dashboard.totals.orders.toLocaleString()}
                      </div>
                    </div>
                    <div className="rounded-xl border border-white/[0.08] bg-white/[0.03] p-4 text-center">
                      <div className="text-[10px] uppercase tracking-wide text-slate-500">AOV</div>
                      <div className="mt-1 text-[22px] font-extrabold text-white">
                        {fmtMoney(dashboard.totals.aov_eur, dashboard.base_currency)}
                      </div>
                    </div>
                  </div>
                </section>

                <section className="mb-8 rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
                  <div className="mb-3 flex items-center justify-between">
                    <h2 className="text-[14px] font-bold text-white">Per-shop breakdown</h2>
                    {dashboard.top_shop && (
                      <span className="text-[10px] text-amber-300">
                        Top: {dashboard.top_shop.label || dashboard.top_shop.shop_domain}
                      </span>
                    )}
                  </div>
                  <div className="space-y-2">
                    {dashboard.members.map((m) => {
                      const share = dashboard.totals.revenue_eur > 0
                        ? Math.round((m.revenue_eur / dashboard.totals.revenue_eur) * 100)
                        : 0;
                      return (
                        <div key={m.shop_domain} className="rounded-xl border border-white/[0.04] bg-white/[0.015] p-3">
                          <div className="flex items-center justify-between gap-3">
                            <div className="min-w-0">
                              <div className="flex items-center gap-2">
                                <span className="text-[12px] font-semibold text-slate-200">
                                  {m.label || m.shop_domain}
                                </span>
                                {m.is_primary && (
                                  <span className="rounded bg-[#d4893a]/15 px-1.5 py-0.5 text-[9px] font-bold uppercase text-[#d4893a]">
                                    primary
                                  </span>
                                )}
                              </div>
                              <div className="mt-1 text-[10px] text-slate-500 truncate">{m.shop_domain}</div>
                            </div>
                            <div className="flex-shrink-0 text-right">
                              <div className="text-[14px] font-bold tabular-nums text-white">{fmtMoney(m.revenue_eur, dashboard.base_currency)}</div>
                              <div className="text-[10px] text-slate-500 tabular-nums">{m.orders} orders · AOV {fmtMoney(m.aov_eur, dashboard.base_currency)}</div>
                            </div>
                          </div>
                          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/[0.05]">
                            <div className="h-full rounded-full bg-emerald-400" style={{ width: `${share}%` }} />
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  <div className="mt-4 flex flex-col gap-2 sm:flex-row">
                    <input
                      type="text"
                      value={newShopDomain}
                      onChange={(e) => setNewShopDomain(e.target.value)}
                      placeholder="store.myshopify.com"
                      aria-label="Shop domain to add"
                      className="min-w-0 flex-1 rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-[12px] text-slate-100 placeholder-slate-500 outline-none focus:border-[#d4893a]/40"
                    />
                    <input
                      type="text"
                      value={newShopLabel}
                      onChange={(e) => setNewShopLabel(e.target.value)}
                      placeholder="Label (e.g. EU store)"
                      aria-label="Shop label"
                      className="min-w-0 sm:w-40 rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-[12px] text-slate-100 placeholder-slate-500 outline-none focus:border-[#d4893a]/40"
                    />
                    <button
                      onClick={addMember}
                      disabled={addingShop || !newShopDomain.trim()}
                      className="rounded-lg border border-[#d4893a]/30 bg-[#d4893a]/15 px-4 py-2 text-[12px] font-bold text-[#d4893a] hover:bg-[#d4893a]/25 disabled:opacity-50"
                    >
                      {addingShop ? "…" : "Add shop"}
                    </button>
                  </div>
                </section>
              </>
            )}
          </>
        )}
      </div>
    </main>
  );
}
