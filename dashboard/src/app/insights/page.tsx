"use client";

import { useEffect, useMemo, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "";

type TrendPoint = {
  day?: string;
  visitors?: number;
  page_views?: number;
  clicks?: number;
  hot_visitors?: number;
};

type Action = {
  visitor?: string;
  page?: string;
  type?: string;
  suggestion?: string;
  impact?: string;
};

type Alert = {
  type?: string;
  message?: string;
  priority?: string;
};

type TopPage = {
  url?: string;
  views?: number;
  visitors?: number;
  avg_dwell?: number;
};

type VisitorScore = {
  visitor_id?: string;
  url?: string;
  dwell?: number;
  scroll?: number;
  clicks?: number;
  conversion_score?: number;
};

type Opportunity = {
  url?: string;
  views?: number;
  visitors?: number;
  avg_dwell?: number;
  avg_scroll?: number;
  clicks?: number;
  signal_type?: string;
  recommended_action?: string;
  priority_score?: number;
  explanation?: string;
};

type RevenueLeak = {
  url: string;
  leak_score: number;
  visitors: number;
  views: number;
  avg_dwell: number;
  avg_scroll: number;
  clicks: number;
  click_rate: number;
  signal_type: string;
  recommended_action: string;
  primary_reason: string;
  urgency: "HIGH" | "MEDIUM" | "LOW";
};

function formatNumber(value: unknown) {
  if (typeof value !== "number" || Number.isNaN(value)) return "0";
  return new Intl.NumberFormat("en-US").format(Math.round(value));
}

function formatDecimal(value: unknown, digits = 1) {
  if (typeof value !== "number" || Number.isNaN(value)) return "0";
  return value.toFixed(digits);
}

function prettyText(value?: string) {
  if (!value) return "—";
  return value
    .toLowerCase()
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function impactClass(value?: string) {
  switch ((value || "").toUpperCase()) {
    case "HIGH":
      return "bg-rose-500/15 text-rose-300 ring-1 ring-rose-400/30";
    case "MEDIUM":
      return "bg-amber-500/15 text-amber-300 ring-1 ring-amber-400/30";
    case "LOW":
      return "bg-cyan-500/15 text-cyan-300 ring-1 ring-cyan-400/30";
    default:
      return "bg-white/5 text-slate-300 ring-1 ring-white/10";
  }
}

function SectionHeader({
  eyebrow,
  title,
  description,
}: {
  eyebrow: string;
  title: string;
  description: string;
}) {
  return (
    <div className="mb-5">
      <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-violet-300/80">
        {eyebrow}
      </div>
      <h2 className="text-xl font-semibold text-white">{title}</h2>
      <p className="mt-1 max-w-2xl text-sm text-slate-400">{description}</p>
    </div>
  );
}

function MetricCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint: string;
}) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/5 p-5 shadow-[0_10px_30px_rgba(0,0,0,0.18)] backdrop-blur-sm">
      <div className="text-sm text-slate-400">{label}</div>
      <div className="mt-3 text-3xl font-semibold text-white">{value}</div>
      <div className="mt-2 text-xs text-slate-500">{hint}</div>
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.03] p-8 text-center text-sm text-slate-400">
      {text}
    </div>
  );
}

function getLeakReason(item: {
  avg_dwell: number;
  avg_scroll: number;
  click_rate: number;
  clicks: number;
  visitors: number;
  signal_type?: string;
}) {
  if (item.avg_dwell >= 35 && item.click_rate < 0.08) {
    return "High attention but weak click-through. Likely CTA, offer, or price friction.";
  }

  if (item.avg_scroll >= 60 && item.clicks <= 1) {
    return "Visitors consume the page deeply but do not act. Product page persuasion is leaking.";
  }

  if (item.visitors >= 8 && item.click_rate < 0.1) {
    return "Traffic is reaching the product, but interest is not turning into action.";
  }

  if ((item.signal_type || "").toLowerCase().includes("scarcity")) {
    return "Interest exists, but urgency is not strong enough to push action.";
  }

  return "Behavior suggests intent is present, but conversion friction is blocking the next step.";
}

function getUrgency(score: number): "HIGH" | "MEDIUM" | "LOW" {
  if (score >= 75) return "HIGH";
  if (score >= 45) return "MEDIUM";
  return "LOW";
}

export default function InsightsPage() {
  const [trend, setTrend] = useState<TrendPoint[]>([]);
  const [actions, setActions] = useState<Action[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [topPages, setTopPages] = useState<TopPage[]>([]);
  const [scores, setScores] = useState<VisitorScore[]>([]);
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);

  useEffect(() => {
    let active = true;

    async function loadAll() {
      try {
        const [
          trendRes,
          actionsRes,
          alertsRes,
          topPagesRes,
          scoresRes,
          oppRes,
          priceRes,
        ] = await Promise.all([
          fetch(`${API_BASE}/analytics/weekly-trend`, { cache: "no-store" }),
          fetch(`${API_BASE}/ai/actions`, { cache: "no-store" }),
          fetch(`${API_BASE}/analytics/alerts`, { cache: "no-store" }),
          fetch(`${API_BASE}/analytics/top-pages`, { cache: "no-store" }),
          fetch(`${API_BASE}/analytics/visitor-scores`, { cache: "no-store" }),
          fetch(`${API_BASE}/analytics/live-opportunities`, { cache: "no-store" }),
          fetch(`${API_BASE}/price-intelligence/top`, { cache: "no-store" }),
        ]);

        const trendJson = trendRes.ok ? await trendRes.json() : { trend: [] };
        const actionsJson = actionsRes.ok ? await actionsRes.json() : { actions: [] };
        const alertsJson = alertsRes.ok ? await alertsRes.json() : { alerts: [] };
        const topPagesJson = topPagesRes.ok ? await topPagesRes.json() : { pages: [] };
        const scoresJson = scoresRes.ok ? await scoresRes.json() : { visitors: [] };
        const oppJson = oppRes.ok ? await oppRes.json() : { opportunities: [] };
const priceJson = priceRes.ok ? await priceRes.json() : [];

        if (!active) return;

        setTrend(Array.isArray(trendJson.trend) ? trendJson.trend : []);
        setActions(Array.isArray(actionsJson.actions) ? actionsJson.actions : []);
        setAlerts(Array.isArray(alertsJson.alerts) ? alertsJson.alerts : []);
        setTopPages(Array.isArray(topPagesJson.pages) ? topPagesJson.pages : []);
        setScores(Array.isArray(scoresJson.visitors) ? scoresJson.visitors : []);
        setOpportunities(Array.isArray(oppJson.opportunities) ? oppJson.opportunities : []);
      } catch {
        // silent fail to keep dashboard resilient
      }
    }

    loadAll();
    const interval = setInterval(loadAll, 8000);

    return () => {
      active = false;
      clearInterval(interval);
    };
  }, []);

  const totals = useMemo(() => {
    return {
      visitors: trend.reduce((acc, item) => acc + (item.visitors || 0), 0),
      pageViews: trend.reduce((acc, item) => acc + (item.page_views || 0), 0),
      clicks: trend.reduce((acc, item) => acc + (item.clicks || 0), 0),
      hotVisitors: trend.reduce((acc, item) => acc + (item.hot_visitors || 0), 0),
    };
  }, [trend]);

  const maxVisitors = useMemo(() => {
    const max = Math.max(...trend.map((p) => p.visitors || 0), 1);
    return max < 1 ? 1 : max;
  }, [trend]);

  const revenueLeaks = useMemo<RevenueLeak[]>(() => {
    const opportunityByUrl = new Map<string, Opportunity>();
    const scoreStatsByUrl = new Map<
      string,
      {
        totalDwell: number;
        totalScroll: number;
        totalClicks: number;
        totalConversionScore: number;
        count: number;
      }
    >();

    for (const opp of opportunities) {
      if (!opp.url) continue;
      opportunityByUrl.set(opp.url, opp);
    }

    for (const score of scores) {
      if (!score.url) continue;
      const current = scoreStatsByUrl.get(score.url) || {
        totalDwell: 0,
        totalScroll: 0,
        totalClicks: 0,
        totalConversionScore: 0,
        count: 0,
      };

      current.totalDwell += score.dwell || 0;
      current.totalScroll += score.scroll || 0;
      current.totalClicks += score.clicks || 0;
      current.totalConversionScore += score.conversion_score || 0;
      current.count += 1;

      scoreStatsByUrl.set(score.url, current);
    }

    const leaks = topPages
      .filter((page) => !!page.url)
      .map((page) => {
        const url = page.url || "";
        const opp = opportunityByUrl.get(url);
        const scoreStats = scoreStatsByUrl.get(url);

        const visitors = page.visitors || opp?.visitors || 0;
        const views = page.views || opp?.views || 0;
        const avgDwell =
          scoreStats && scoreStats.count > 0
            ? scoreStats.totalDwell / scoreStats.count
            : page.avg_dwell || opp?.avg_dwell || 0;

        const avgScroll =
          scoreStats && scoreStats.count > 0
            ? scoreStats.totalScroll / scoreStats.count
            : opp?.avg_scroll || 0;

        const clicks =
          (scoreStats?.totalClicks || 0) > 0
            ? scoreStats?.totalClicks || 0
            : opp?.clicks || 0;

        const clickRate = views > 0 ? clicks / views : 0;
        const priority = opp?.priority_score || 0;
        const signalType = opp?.signal_type || "conversion_friction";
        const recommendedAction =
          opp?.recommended_action || "Improve CTA clarity, offer framing, and urgency.";

        const trafficWeight = Math.min(visitors * 3, 30);
        const attentionWeight = Math.min(avgDwell * 0.7, 25);
        const scrollWeight = Math.min(avgScroll * 0.25, 20);
        const opportunityWeight = Math.min(priority * 1.2, 20);
        const frictionBonus = clickRate < 0.08 ? 18 : clickRate < 0.12 ? 10 : 0;
        const clickPenalty = Math.min(clicks * 2, 15);

        const rawScore =
          trafficWeight +
          attentionWeight +
          scrollWeight +
          opportunityWeight +
          frictionBonus -
          clickPenalty;

        const leakScore = Math.max(0, Math.min(100, rawScore));
        const primaryReason = getLeakReason({
          avg_dwell: avgDwell,
          avg_scroll: avgScroll,
          click_rate: clickRate,
          clicks,
          visitors,
          signal_type: signalType,
        });
        const urgency = getUrgency(leakScore);

        return {
          url,
          leak_score: leakScore,
          visitors,
          views,
          avg_dwell: avgDwell,
          avg_scroll: avgScroll,
          clicks,
          click_rate: clickRate,
          signal_type: signalType,
          recommended_action: recommendedAction,
          primary_reason: primaryReason,
          urgency,
        };
      })
      .filter((item) => item.visitors > 0 || item.views > 0)
      .filter((item) => item.leak_score >= 25)
      .sort((a, b) => b.leak_score - a.leak_score)
      .slice(0, 6);

    return leaks;
  }, [topPages, opportunities, scores]);

  const leakTotals = useMemo(() => {
    return {
      high: revenueLeaks.filter((item) => item.urgency === "HIGH").length,
      medium: revenueLeaks.filter((item) => item.urgency === "MEDIUM").length,
      low: revenueLeaks.filter((item) => item.urgency === "LOW").length,
      topScore: revenueLeaks.length > 0 ? revenueLeaks[0].leak_score : 0,
    };
  }, [revenueLeaks]);

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top,rgba(139,92,246,0.16),transparent_30%),linear-gradient(180deg,#020617_0%,#0f172a_100%)] text-white">
      <div className="mx-auto max-w-7xl px-6 py-10 lg:px-8">
        <div className="mb-8 overflow-hidden rounded-3xl border border-white/10 bg-[linear-gradient(135deg,rgba(2,6,23,0.98)_0%,rgba(15,23,42,0.96)_35%,rgba(30,27,75,0.94)_70%,rgba(88,28,135,0.88)_100%)] shadow-[0_24px_80px_rgba(0,0,0,0.38)] backdrop-blur-xl">
          <div className="px-6 py-5 lg:px-8">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-violet-300/80">
                  Pro Dashboard
                </div>
                <h1 className="mt-2 text-4xl font-semibold text-white">
                  HedgeSpark Intelligence
                </h1>
                <p className="mt-2 max-w-2xl text-sm text-slate-300">
                  Advanced behavior intelligence, weekly trend analysis, alerts, AI actions, and Revenue Leak detection.
                </p>
              </div>

              <a
                href="/app"
                className="rounded-full border border-white/10 bg-white/5 px-4 py-2 text-sm font-medium text-slate-200 transition hover:bg-white/10"
              >
                Lite Dashboard
              </a>
            </div>
          </div>
        </div>

        <section className="mb-8">
          <SectionHeader
            eyebrow="Pro Overview"
            title="Intelligence snapshot"
            description="The advanced layer of HedgeSpark: weekly behavior, live signals, AI decision support, and revenue leak detection."
          />

          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <MetricCard
              label="Weekly Visitors"
              value={formatNumber(totals.visitors)}
              hint="Last 7 tracked days"
            />
            <MetricCard
              label="Weekly Page Views"
              value={formatNumber(totals.pageViews)}
              hint="Behavior volume"
            />
            <MetricCard
              label="Weekly Clicks"
              value={formatNumber(totals.clicks)}
              hint="Engagement actions"
            />
            <MetricCard
              label="Weekly Hot Visitors"
              value={formatNumber(totals.hotVisitors)}
              hint="High-intent activity"
            />
          </div>
        </section>

        <section className="mb-8">
          <SectionHeader
            eyebrow="Revenue Leak Detector"
            title="Where the store is losing conversion energy"
            description="Products/pages attracting attention but failing to turn it into decisive action."
          />

          <div className="mb-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <MetricCard
              label="Leak Candidates"
              value={formatNumber(revenueLeaks.length)}
              hint="Pages with measurable conversion friction"
            />
            <MetricCard
              label="High Urgency Leaks"
              value={formatNumber(leakTotals.high)}
              hint="Most urgent conversion losses"
            />
            <MetricCard
              label="Medium Urgency Leaks"
              value={formatNumber(leakTotals.medium)}
              hint="Recoverable with rapid experiments"
            />
            <MetricCard
              label="Top Leak Score"
              value={formatDecimal(leakTotals.topScore, 0)}
              hint="Highest current risk detected"
            />
          </div>

          {revenueLeaks.length === 0 ? (
            <EmptyState text="No strong revenue leaks detected yet. As more behavioral data arrives, HedgeSpark will surface the first leakage points here." />
          ) : (
            <div className="grid gap-4 xl:grid-cols-2">
              {revenueLeaks.map((item) => (
                <div
                  key={item.url}
                  className="rounded-3xl border border-rose-400/15 bg-[linear-gradient(180deg,rgba(255,255,255,0.04),rgba(255,255,255,0.02))] p-5 shadow-[0_20px_60px_rgba(0,0,0,0.25)]"
                >
                  <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
                    <div className="min-w-0 flex-1">
                      <div className="mb-2 flex flex-wrap items-center gap-2">
                        <span
                          className={`rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide ${impactClass(
                            item.urgency
                          )}`}
                        >
                          {item.urgency} Urgency
                        </span>
                        <span className="rounded-full bg-white/5 px-2.5 py-1 text-[11px] text-slate-300 ring-1 ring-white/10">
                          Leak Score {formatDecimal(item.leak_score, 0)}
                        </span>
                        <span className="rounded-full bg-white/5 px-2.5 py-1 text-[11px] text-slate-300 ring-1 ring-white/10">
                          {prettyText(item.signal_type)}
                        </span>
                      </div>

                      <div className="truncate text-lg font-semibold text-white">
                        {item.url}
                      </div>

                      <p className="mt-3 text-sm leading-6 text-slate-300">
                        {item.primary_reason}
                      </p>

                      <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                        <div className="rounded-2xl border border-white/10 bg-white/5 p-3">
                          <div className="text-[11px] uppercase tracking-wide text-slate-400">
                            Visitors
                          </div>
                          <div className="mt-1 text-xl font-semibold text-white">
                            {formatNumber(item.visitors)}
                          </div>
                        </div>

                        <div className="rounded-2xl border border-white/10 bg-white/5 p-3">
                          <div className="text-[11px] uppercase tracking-wide text-slate-400">
                            Views
                          </div>
                          <div className="mt-1 text-xl font-semibold text-white">
                            {formatNumber(item.views)}
                          </div>
                        </div>

                        <div className="rounded-2xl border border-white/10 bg-white/5 p-3">
                          <div className="text-[11px] uppercase tracking-wide text-slate-400">
                            Clicks
                          </div>
                          <div className="mt-1 text-xl font-semibold text-white">
                            {formatNumber(item.clicks)}
                          </div>
                        </div>

                        <div className="rounded-2xl border border-white/10 bg-white/5 p-3">
                          <div className="text-[11px] uppercase tracking-wide text-slate-400">
                            Avg Dwell
                          </div>
                          <div className="mt-1 text-xl font-semibold text-white">
                            {formatDecimal(item.avg_dwell, 1)}s
                          </div>
                        </div>

                        <div className="rounded-2xl border border-white/10 bg-white/5 p-3">
                          <div className="text-[11px] uppercase tracking-wide text-slate-400">
                            Avg Scroll
                          </div>
                          <div className="mt-1 text-xl font-semibold text-white">
                            {formatDecimal(item.avg_scroll, 0)}%
                          </div>
                        </div>

                        <div className="rounded-2xl border border-white/10 bg-white/5 p-3">
                          <div className="text-[11px] uppercase tracking-wide text-slate-400">
                            Click Rate
                          </div>
                          <div className="mt-1 text-xl font-semibold text-white">
                            {formatDecimal(item.click_rate * 100, 1)}%
                          </div>
                        </div>
                      </div>

                      <div className="mt-4 rounded-2xl border border-emerald-400/15 bg-emerald-500/5 p-4">
                        <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-emerald-300/80">
                          Suggested recovery action
                        </div>
                        <div className="mt-2 text-sm leading-6 text-slate-200">
                          {item.recommended_action}
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="mb-8 rounded-3xl border border-cyan-400/15 bg-white/[0.04] p-6 shadow-[0_20px_60px_rgba(0,0,0,0.25)]">
          <SectionHeader
            eyebrow="Weekly Trend"
            title="Traffic and attention across the week"
            description="A compact visual snapshot of visitor volume over the last tracked days."
          />

          {trend.length === 0 ? (
            <EmptyState text="No weekly trend data available yet." />
          ) : (
            <div className="grid gap-3 md:grid-cols-7">
              {trend.map((point, index) => {
                const value = point.visitors || 0;
                const height = Math.max(16, Math.round((value / maxVisitors) * 180));
                return (
                  <div
                    key={`${point.day || "day"}-${index}`}
                    className="rounded-2xl border border-white/10 bg-white/5 p-4"
                  >
                    <div className="mb-3 text-xs text-slate-400">{point.day || `Day ${index + 1}`}</div>
                    <div className="flex h-48 items-end">
                      <div
                        className="w-full rounded-xl bg-[linear-gradient(180deg,rgba(168,85,247,0.95),rgba(34,211,238,0.75))]"
                        style={{ height }}
                      />
                    </div>
                    <div className="mt-3 text-lg font-semibold text-white">
                      {formatNumber(value)}
                    </div>
                    <div className="text-xs text-slate-500">Visitors</div>
                  </div>
                );
              })}
            </div>
          )}
        </section>

        <section className="mb-8">
          <SectionHeader
            eyebrow="Live Alerts"
            title="Signals requiring attention"
            description="What HedgeSpark currently considers worth surfacing to the merchant."
          />

          {alerts.length === 0 ? (
            <EmptyState text="No live alerts right now." />
          ) : (
            <div className="grid gap-4 lg:grid-cols-3">
              {alerts.slice(0, 6).map((alert, index) => (
                <div
                  key={`${alert.type || "alert"}-${index}`}
                  className="rounded-2xl border border-white/10 bg-white/5 p-5"
                >
                  <div className="mb-3 flex items-center gap-2">
                    <span
                      className={`rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide ${impactClass(
                        alert.priority
                      )}`}
                    >
                      {alert.priority || "Info"}
                    </span>
                    <span className="text-xs text-slate-400">{prettyText(alert.type)}</span>
                  </div>
                  <div className="text-sm leading-6 text-slate-200">
                    {alert.message || "—"}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="mb-8">
          <SectionHeader
            eyebrow="AI Actions"
            title="Suggested merchant moves"
            description="Action-oriented suggestions generated from current behavioral signals."
          />

          {actions.length === 0 ? (
            <EmptyState text="No AI actions available yet." />
          ) : (
            <div className="grid gap-4 lg:grid-cols-2">
              {actions.slice(0, 6).map((action, index) => (
                <div
                  key={`${action.page || "action"}-${index}`}
                  className="rounded-2xl border border-white/10 bg-white/5 p-5"
                >
                  <div className="mb-3 flex flex-wrap items-center gap-2">
                    <span
                      className={`rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide ${impactClass(
                        action.impact
                      )}`}
                    >
                      {action.impact || "Medium"}
                    </span>
                    <span className="text-xs text-slate-400">{prettyText(action.type)}</span>
                  </div>

                  <div className="truncate text-sm font-semibold text-white">
                    {action.page || "—"}
                  </div>
                  <div className="mt-2 text-sm leading-6 text-slate-300">
                    {action.suggestion || "—"}
                  </div>

                  {action.visitor ? (
                    <div className="mt-3 text-xs text-slate-500">
                      Visitor: {action.visitor}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="mb-8">
          <SectionHeader
            eyebrow="Top Pages"
            title="Where users spend time"
            description="Highest-traffic surfaces currently tracked by HedgeSpark."
          />

          {topPages.length === 0 ? (
            <EmptyState text="No page intelligence available yet." />
          ) : (
            <div className="overflow-hidden rounded-3xl border border-white/10 bg-white/5">
              <div className="overflow-x-auto">
                <table className="min-w-full text-left text-sm">
                  <thead className="bg-white/5 text-slate-400">
                    <tr>
                      <th className="px-4 py-3 font-medium">URL</th>
                      <th className="px-4 py-3 font-medium">Views</th>
                      <th className="px-4 py-3 font-medium">Visitors</th>
                      <th className="px-4 py-3 font-medium">Avg Dwell</th>
                    </tr>
                  </thead>
                  <tbody>
                    {topPages.slice(0, 10).map((page, index) => (
                      <tr
                        key={`${page.url || "page"}-${index}`}
                        className="border-t border-white/10"
                      >
                        <td className="max-w-[420px] truncate px-4 py-3 text-slate-200">
                          {page.url || "—"}
                        </td>
                        <td className="px-4 py-3 text-slate-300">
                          {formatNumber(page.views)}
                        </td>
                        <td className="px-4 py-3 text-slate-300">
                          {formatNumber(page.visitors)}
                        </td>
                        <td className="px-4 py-3 text-slate-300">
                          {formatDecimal(page.avg_dwell, 1)}s
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </section>

        <section className="mb-8">
          <SectionHeader
            eyebrow="Visitor Scores"
            title="Intent-rich visitor samples"
            description="A quick look at scored sessions contributing to the intelligence layer."
          />

          {scores.length === 0 ? (
            <EmptyState text="No visitor scores available yet." />
          ) : (
            <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
              {scores.slice(0, 9).map((score, index) => (
                <div
                  key={`${score.visitor_id || "visitor"}-${index}`}
                  className="rounded-2xl border border-white/10 bg-white/5 p-5"
                >
                  <div className="truncate text-sm font-semibold text-white">
                    {score.url || "—"}
                  </div>
                  <div className="mt-3 grid grid-cols-2 gap-3">
                    <div>
                      <div className="text-[11px] uppercase tracking-wide text-slate-400">
                        Dwell
                      </div>
                      <div className="mt-1 text-lg font-semibold text-white">
                        {formatDecimal(score.dwell, 1)}s
                      </div>
                    </div>
                    <div>
                      <div className="text-[11px] uppercase tracking-wide text-slate-400">
                        Scroll
                      </div>
                      <div className="mt-1 text-lg font-semibold text-white">
                        {formatDecimal(score.scroll, 0)}%
                      </div>
                    </div>
                    <div>
                      <div className="text-[11px] uppercase tracking-wide text-slate-400">
                        Clicks
                      </div>
                      <div className="mt-1 text-lg font-semibold text-white">
                        {formatNumber(score.clicks)}
                      </div>
                    </div>
                    <div>
                      <div className="text-[11px] uppercase tracking-wide text-slate-400">
                        Conv. Score
                      </div>
                      <div className="mt-1 text-lg font-semibold text-white">
                        {formatDecimal(score.conversion_score, 1)}
                      </div>
                    </div>
                  </div>

                  {score.visitor_id ? (
                    <div className="mt-3 truncate text-xs text-slate-500">
                      Visitor ID: {score.visitor_id}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="pb-4">
          <SectionHeader
            eyebrow="Live Opportunities"
            title="Current conversion opportunities"
            description="Pages or products where HedgeSpark sees room to increase conversion pressure."
          />

          {opportunities.length === 0 ? (
            <EmptyState text="No live opportunities available yet." />
          ) : (
            <div className="grid gap-4 xl:grid-cols-2">
              {opportunities.slice(0, 8).map((opp, index) => (
                <div
                  key={`${opp.url || "opportunity"}-${index}`}
                  className="rounded-2xl border border-white/10 bg-white/5 p-5"
                >
                  <div className="mb-3 flex flex-wrap items-center gap-2">
                    <span className="rounded-full bg-white/5 px-2.5 py-1 text-[11px] text-slate-300 ring-1 ring-white/10">
                      {prettyText(opp.signal_type)}
                    </span>
                    <span className="rounded-full bg-violet-500/15 px-2.5 py-1 text-[11px] text-violet-200 ring-1 ring-violet-400/20">
                      Priority {formatDecimal(opp.priority_score, 0)}
                    </span>
                  </div>

                  <div className="truncate text-lg font-semibold text-white">
                    {opp.url || "—"}
                  </div>

                  <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                    <div className="rounded-2xl border border-white/10 bg-white/5 p-3">
                      <div className="text-[11px] uppercase tracking-wide text-slate-400">
                        Views
                      </div>
                      <div className="mt-1 text-xl font-semibold text-white">
                        {formatNumber(opp.views)}
                      </div>
                    </div>

                    <div className="rounded-2xl border border-white/10 bg-white/5 p-3">
                      <div className="text-[11px] uppercase tracking-wide text-slate-400">
                        Visitors
                      </div>
                      <div className="mt-1 text-xl font-semibold text-white">
                        {formatNumber(opp.visitors)}
                      </div>
                    </div>

                    <div className="rounded-2xl border border-white/10 bg-white/5 p-3">
                      <div className="text-[11px] uppercase tracking-wide text-slate-400">
                        Clicks
                      </div>
                      <div className="mt-1 text-xl font-semibold text-white">
                        {formatNumber(opp.clicks)}
                      </div>
                    </div>

                    <div className="rounded-2xl border border-white/10 bg-white/5 p-3">
                      <div className="text-[11px] uppercase tracking-wide text-slate-400">
                        Avg Dwell
                      </div>
                      <div className="mt-1 text-xl font-semibold text-white">
                        {formatDecimal(opp.avg_dwell, 1)}s
                      </div>
                    </div>

                    <div className="rounded-2xl border border-white/10 bg-white/5 p-3">
                      <div className="text-[11px] uppercase tracking-wide text-slate-400">
                        Avg Scroll
                      </div>
                      <div className="mt-1 text-xl font-semibold text-white">
                        {formatDecimal(opp.avg_scroll, 0)}%
                      </div>
                    </div>
                  </div>

                  <div className="mt-4 rounded-2xl border border-cyan-400/15 bg-cyan-500/5 p-4">
                    <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-cyan-300/80">
                      Recommended action
                    </div>
                    <div className="mt-2 text-sm leading-6 text-slate-200">
                      {opp.recommended_action || "—"}
                    </div>
                  </div>

                  {opp.explanation ? (
                    <div className="mt-3 text-sm leading-6 text-slate-400">
                      {opp.explanation}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
