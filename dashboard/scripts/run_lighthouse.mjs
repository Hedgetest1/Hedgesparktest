#!/usr/bin/env node
/**
 * run_lighthouse.mjs — Tier 6.3 Lighthouse budget gate.
 *
 * Runs Lighthouse (desktop preset) against each route listed in
 * `dashboard/lighthouse-budget.json` and asserts every category score
 * stays at or above the reviewed budget. Fails fast on the first
 * route that drops below any threshold.
 *
 * Why
 * ---
 * Perf, a11y, best-practices and SEO drift silently: one careless
 * import pulls in a heavy lib; one "use client" gate returns null and
 * the server renders blank HTML to Google's crawler (that was the
 * real bug this gate caught on its baseline run). Without a hard
 * threshold we only notice in hindsight.
 *
 * Usage
 * -----
 *   node scripts/run_lighthouse.mjs
 *   node scripts/run_lighthouse.mjs --url http://localhost:3000 --only /
 *
 * Environment
 * -----------
 *   LH_BASE_URL   — override base URL (default http://127.0.0.1:3000)
 *   CHROME_PATH   — chromium binary path (auto-resolved from Playwright
 *                   install if unset)
 *
 * Dependencies: lighthouse + chrome-launcher (devDependencies).
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import lighthouse from "lighthouse";
import * as chromeLauncher from "chrome-launcher";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DASHBOARD = path.resolve(__dirname, "..");
const BUDGET_FILE = path.join(DASHBOARD, "lighthouse-budget.json");

function parseArgs() {
  const args = process.argv.slice(2);
  const out = {
    baseUrl: process.env.LH_BASE_URL || "http://127.0.0.1:3000",
    only: null,
    json: false,  // --json: emit machine-readable summary to stdout
  };
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--url") out.baseUrl = args[++i];
    else if (args[i] === "--only") out.only = args[++i];
    else if (args[i] === "--json") out.json = true;
  }
  return out;
}

function loadBudget() {
  if (!fs.existsSync(BUDGET_FILE)) {
    console.error(`run_lighthouse: budget file missing at ${BUDGET_FILE}`);
    process.exit(2);
  }
  return JSON.parse(fs.readFileSync(BUDGET_FILE, "utf8"));
}

function resolveChromePath() {
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  // Fall back to the Playwright chromium bundle already installed for
  // the e2e suite — we don't want a second browser install.
  const pwCache = "/root/.cache/ms-playwright";
  if (!fs.existsSync(pwCache)) return undefined;
  const chromiumDirs = fs.readdirSync(pwCache).filter((d) => d.startsWith("chromium-"));
  if (chromiumDirs.length === 0) return undefined;
  const candidate = path.join(pwCache, chromiumDirs[0], "chrome-linux64", "chrome");
  return fs.existsSync(candidate) ? candidate : undefined;
}

const CATEGORIES = [
  { id: "performance", label: "performance", budgetKey: "performance" },
  { id: "accessibility", label: "accessibility", budgetKey: "accessibility" },
  { id: "best-practices", label: "best_practices", budgetKey: "best_practices" },
  { id: "seo", label: "seo", budgetKey: "seo" },
];

async function main() {
  const { baseUrl, only, json: jsonMode } = parseArgs();
  const cfg = loadBudget();
  const routes = only ? [only] : cfg.routes;
  const budgets = cfg.budgets;

  // In JSON mode we suppress the human-readable progress prints so stdout
  // is clean JSON. Stderr still carries errors. This lets the Python
  // wrapper parse stdout directly without stripping ANSI / column headers.
  const log = jsonMode ? () => {} : (...a) => console.log(...a);

  const chromePath = resolveChromePath();
  if (chromePath) process.env.CHROME_PATH = chromePath;

  const chrome = await chromeLauncher.launch({
    chromeFlags: ["--headless", "--no-sandbox", "--disable-gpu"],
  });

  const opts = {
    logLevel: "error",
    output: "json",
    port: chrome.port,
    onlyCategories: ["performance", "accessibility", "best-practices", "seo"],
  };

  const lhCfg = {
    extends: "lighthouse:default",
    settings: {
      formFactor: "desktop",
      screenEmulation: {
        mobile: false,
        width: 1350,
        height: 940,
        deviceScaleFactor: 1,
        disabled: false,
      },
      throttling: { rttMs: 40, throughputKbps: 10 * 1024, cpuSlowdownMultiplier: 1 },
      pauseAfterLoadMs: 1500,
      networkQuietThresholdMs: 1500,
    },
  };

  const failures = [];
  // In JSON mode we collect per-route audit metrics — the Python cron
  // wrapper needs LCP/CLS/TBT specifically (not just category scores)
  // for slow-trend analysis.
  const routeResults = [];
  log(`run_lighthouse: base=${baseUrl} routes=${routes.length}`);
  log(
    `budgets: P≥${budgets.performance}  A≥${budgets.accessibility}  BP≥${budgets.best_practices}  SEO≥${budgets.seo}`,
  );
  log();

  try {
    for (const route of routes) {
      const url = baseUrl.replace(/\/$/, "") + route;
      const result = await lighthouse(url, opts, lhCfg);
      const lhr = result.lhr;
      const scores = {};
      for (const cat of CATEGORIES) {
        const c = lhr.categories[cat.id];
        scores[cat.label] = c?.score != null ? Math.round(c.score * 100) : null;
      }
      const s = scores;
      const marks = CATEGORIES.map((c) => {
        const actual = s[c.label];
        const budget = budgets[c.budgetKey];
        const bad = actual == null || actual < budget;
        if (bad) failures.push({ route, cat: c.label, actual, budget });
        return `${c.label.slice(0, 3).toUpperCase()}:${actual ?? "ERR"}${bad ? "✗" : ""}`;
      }).join("  ");
      log(`  ${route.padEnd(12)}  ${marks}`);

      if (jsonMode) {
        // Core Web Vitals + perf sub-metrics. `numericValue` is always
        // in ms for these audits. Missing audits surface as null.
        const a = lhr.audits || {};
        const pick = (id) => a[id]?.numericValue ?? null;
        routeResults.push({
          route,
          scores: s,
          metrics: {
            lcp_ms: pick("largest-contentful-paint"),
            fcp_ms: pick("first-contentful-paint"),
            tbt_ms: pick("total-blocking-time"),
            tti_ms: pick("interactive"),
            cls:    a["cumulative-layout-shift"]?.numericValue ?? null,
            si_ms:  pick("speed-index"),
          },
        });
      }
    }
  } finally {
    await chrome.kill();
  }

  if (jsonMode) {
    // Single JSON blob to stdout — consumed by lighthouse_monitor.py.
    process.stdout.write(JSON.stringify({
      base_url: baseUrl,
      generated_at: new Date().toISOString(),
      budgets,
      routes: routeResults,
      failures,
    }) + "\n");
    // Exit 0 even on failures in JSON mode — the wrapper decides what to
    // do with regressions based on historical baselines, not a hard gate.
    process.exit(0);
  }

  if (failures.length > 0) {
    console.log();
    console.log(`FAIL: ${failures.length} budget(s) exceeded`);
    for (const f of failures) {
      const delta = f.actual == null ? "ERR" : `${f.actual} < ${f.budget} (${f.actual - f.budget})`;
      console.log(`  ${f.route}  ${f.cat}: ${delta}`);
    }
    console.log();
    console.log(
      `If the regression is intentional, update ${path.basename(BUDGET_FILE)} with a reviewed delta.`,
    );
    process.exit(1);
  }

  console.log();
  console.log("OK: all Lighthouse budgets within cap.");
}

main().catch((err) => {
  console.error("run_lighthouse failed:", err);
  process.exit(2);
});
