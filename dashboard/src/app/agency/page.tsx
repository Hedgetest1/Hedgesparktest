"use client";

/**
 * /agency — White-label agency console.
 *
 * Email-gated agency dashboard. Lists client shops with revshare €€
 * rollup. Real data from /agency/* endpoints, X-Agency-Email header
 * authentication.
 *
 * Production should swap the email gate for a proper agency JWT —
 * for now the email is stored in localStorage and resent on each request.
 */

import { useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "https://api.hedgesparkhq.com";
const STORAGE_KEY = "hs_agency_email";

type ClientRow = {
  shop_domain: string;
  nickname: string | null;
  status: string;
  revshare_pct: number;
  revenue_eur: number;
  orders: number;
  aov_eur: number;
  revshare_eur: number;
};

type AgencyDashboard = {
  agency_id: number;
  name: string;
  lookback_days: number;
  clients: ClientRow[];
  totals: { revenue_eur: number; revshare_eur: number; client_count: number };
  top_client: ClientRow | null;
};

function fmtMoney(n: number): string {
  if (n === 0) return "€0";
  const a = Math.abs(n);
  if (a >= 1000) return "€" + (a / 1000).toFixed(a >= 10_000 ? 0 : 1) + "k";
  return "€" + Math.round(a);
}

export default function AgencyPage() {
  const [email, setEmail] = useState<string>("");
  const [pendingEmail, setPendingEmail] = useState("");
  const [dashboard, setDashboard] = useState<AgencyDashboard | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newShopDomain, setNewShopDomain] = useState("");
  const [newNickname, setNewNickname] = useState("");
  const [newRevshare, setNewRevshare] = useState("20");

  useEffect(() => {
    if (typeof window === "undefined") return;
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored) setEmail(stored);
  }, []);

  const headers = (): HeadersInit => ({
    "Content-Type": "application/json",
    ...(email ? { "X-Agency-Email": email } : {}),
  });

  const loadDashboard = async () => {
    if (!email) return;
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(`${API_BASE}/agency/dashboard`, { headers: headers() });
      if (r.status === 404) {
        setError("No agency registered for this email. Register one first.");
        setDashboard(null);
        return;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j: AgencyDashboard = await r.json();
      setDashboard(j);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadDashboard(); /* eslint-disable-next-line */ }, [email]);

  const saveEmail = () => {
    if (!pendingEmail.trim()) return;
    window.localStorage.setItem(STORAGE_KEY, pendingEmail.trim());
    setEmail(pendingEmail.trim());
    setPendingEmail("");
  };

  const registerAgency = async () => {
    if (!email) return;
    try {
      await fetch(`${API_BASE}/agency/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: email.split("@")[0],
          contact_email: email,
        }),
      });
      await loadDashboard();
    } catch {}
  };

  const addClient = async () => {
    if (!newShopDomain.trim()) return;
    try {
      await fetch(`${API_BASE}/agency/clients`, {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({
          shop_domain: newShopDomain.trim(),
          nickname: newNickname.trim() || null,
          revshare_pct: parseFloat(newRevshare) || 20,
        }),
      });
      setNewShopDomain("");
      setNewNickname("");
      await loadDashboard();
    } catch {}
  };

  if (!email) {
    return (
      <main className="min-h-screen bg-[#0a0a0c] px-6 py-16 text-slate-100">
        <div className="mx-auto max-w-md">
          <a
            href="/"
            className="text-[12px] font-semibold uppercase tracking-[0.18em] text-[#e8a04e]"
          >
            Hedge Spark
          </a>
          <h1 className="mt-2 text-[28px] font-extrabold text-white">Agency console</h1>
          <p className="mt-1 text-[14px] text-slate-400">Sign in with your agency email to see your client roster.</p>
          <div className="mt-6 flex gap-2">
            <input
              type="email"
              value={pendingEmail}
              onChange={(e) => setPendingEmail(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && saveEmail()}
              placeholder="agency@yourcompany.com"
              aria-label="Agency email"
              className="min-w-0 flex-1 rounded-lg border border-white/[0.1] bg-black/30 px-3 py-2 text-[14px] text-white placeholder-slate-500 outline-none focus:border-[#d4893a]/60"
            />
            <button
              onClick={saveEmail}
              disabled={!pendingEmail.trim()}
              className="rounded-lg bg-[#d4893a] px-4 py-2 text-[14px] font-bold text-black hover:bg-[#e8a04e] disabled:opacity-50"
            >
              Continue
            </button>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-[#0a0a0c] px-6 py-10 text-slate-100">
      <div className="mx-auto max-w-6xl">
        <header className="mb-8 flex items-start justify-between gap-4">
          <div>
            <a href="/" className="text-[12px] font-semibold uppercase tracking-[0.18em] text-[#e8a04e]">
              Hedge Spark
            </a>
            <h1 className="mt-2 text-[28px] font-extrabold tracking-tight text-white sm:text-[34px]">
              Agency Console
            </h1>
            <p className="mt-1 text-[14px] text-slate-400">{email}</p>
          </div>
          <button
            onClick={() => {
              window.localStorage.removeItem(STORAGE_KEY);
              setEmail("");
              setDashboard(null);
            }}
            className="rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-[11px] text-slate-400 hover:border-white/[0.16] hover:text-white"
          >
            Sign out
          </button>
        </header>

        {loading && (
          <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-8 text-center text-[13px] text-slate-500">
            Loading…
          </div>
        )}

        {error && (
          <div className="rounded-2xl border border-rose-400/20 bg-rose-500/[0.05] p-4 text-[13px] text-rose-300">
            {error}
            {error.includes("No agency registered") && (
              <button
                onClick={registerAgency}
                className="ml-3 rounded-md border border-[#d4893a]/40 bg-[#d4893a]/15 px-3 py-1 text-[11px] font-bold text-[#d4893a] hover:bg-[#d4893a]/25"
              >
                Register {email}
              </button>
            )}
          </div>
        )}

        {dashboard && (
          <>
            <section className="mb-6 rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
              <h2 className="text-[14px] font-bold text-white">{dashboard.name} — {dashboard.lookback_days}d</h2>
              <div className="mt-3 grid grid-cols-3 gap-3">
                <div className="rounded-xl border border-emerald-400/20 bg-emerald-500/[0.06] p-4 text-center">
                  <div className="text-[10px] uppercase tracking-wide text-emerald-400">Client revenue</div>
                  <div className="mt-1 text-[22px] font-extrabold text-emerald-300">{fmtMoney(dashboard.totals.revenue_eur)}</div>
                </div>
                <div className="rounded-xl border border-amber-400/20 bg-amber-500/[0.06] p-4 text-center">
                  <div className="text-[10px] uppercase tracking-wide text-amber-400">Your revshare</div>
                  <div className="mt-1 text-[22px] font-extrabold text-amber-300">{fmtMoney(dashboard.totals.revshare_eur)}</div>
                </div>
                <div className="rounded-xl border border-white/[0.08] bg-white/[0.03] p-4 text-center">
                  <div className="text-[10px] uppercase tracking-wide text-slate-500">Active clients</div>
                  <div className="mt-1 text-[22px] font-extrabold text-white">{dashboard.totals.client_count}</div>
                </div>
              </div>
            </section>

            <section className="mb-8 rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
              <h2 className="mb-3 text-[14px] font-bold text-white">Client roster</h2>
              {dashboard.clients.length === 0 ? (
                <div className="rounded-xl border border-dashed border-white/[0.08] bg-white/[0.01] p-4 text-center text-[12px] text-slate-500">
                  No clients yet. Add one below.
                </div>
              ) : (
                <div className="space-y-2">
                  {dashboard.clients.map((c) => (
                    <div key={c.shop_domain} className="rounded-xl border border-white/[0.04] bg-white/[0.015] p-3">
                      <div className="flex items-center justify-between gap-3">
                        <div className="min-w-0">
                          <div className="text-[12px] font-semibold text-slate-200">
                            {c.nickname || c.shop_domain}
                          </div>
                          <div className="mt-1 text-[10px] text-slate-500 truncate">
                            {c.shop_domain} · revshare {c.revshare_pct}%
                          </div>
                        </div>
                        <div className="flex-shrink-0 text-right">
                          <div className="text-[14px] font-bold tabular-nums text-white">{fmtMoney(c.revenue_eur)}</div>
                          <div className="text-[11px] tabular-nums text-amber-300">+{fmtMoney(c.revshare_eur)} revshare</div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-4">
                <input
                  type="text"
                  value={newShopDomain}
                  onChange={(e) => setNewShopDomain(e.target.value)}
                  placeholder="client.myshopify.com"
                  aria-label="Client shop domain"
                  className="rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-[12px] text-slate-100 placeholder-slate-500 outline-none focus:border-[#d4893a]/40 sm:col-span-2"
                />
                <input
                  type="text"
                  value={newNickname}
                  onChange={(e) => setNewNickname(e.target.value)}
                  placeholder="Nickname"
                  aria-label="Nickname"
                  className="rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-[12px] text-slate-100 placeholder-slate-500 outline-none focus:border-[#d4893a]/40"
                />
                <div className="flex gap-2">
                  <input
                    type="number"
                    value={newRevshare}
                    onChange={(e) => setNewRevshare(e.target.value)}
                    placeholder="%"
                    aria-label="Revshare percent"
                    className="w-16 rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-[12px] text-slate-100 outline-none focus:border-[#d4893a]/40"
                  />
                  <button
                    onClick={addClient}
                    disabled={!newShopDomain.trim()}
                    className="flex-1 rounded-lg border border-[#d4893a]/30 bg-[#d4893a]/15 px-3 py-2 text-[12px] font-bold text-[#d4893a] hover:bg-[#d4893a]/25 disabled:opacity-50"
                  >
                    Add
                  </button>
                </div>
              </div>
            </section>
          </>
        )}
      </div>
    </main>
  );
}
