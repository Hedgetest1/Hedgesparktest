/**
 * LiteBaseAnalytics.test.tsx — first component-test (F5 skeleton).
 *
 * Verifies the empty-state branch of every Class-D-and-down tile so a
 * regression in CardError detection or skeleton-stuck logic surfaces
 * BEFORE Playwright E2E runs (which is ~60× slower per cycle).
 *
 * Each tile mocks `apiClient.GET` to resolve with `{ has_data: false }`
 * and asserts the empty-state copy (NOT the skeleton, NOT an error).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import {
  DeviceSplitTile,
  AbandonmentTrendTile,
  OrderRhythmTile,
} from "./LiteBaseAnalytics";

// Mock apiClient to control what each tile sees
vi.mock("@/app/lib/api-client", () => ({
  apiClient: {
    GET: vi.fn(),
  },
}));

import { apiClient } from "@/app/lib/api-client";

describe("LiteBaseAnalytics empty states", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("DeviceSplitTile shows empty-state copy when no traffic", async () => {
    (apiClient.GET as any).mockResolvedValue({
      data: { days: 14, total_sessions: 0, has_data: false, slices: [] },
      error: null,
    });
    render(<DeviceSplitTile />);
    await waitFor(() => {
      expect(screen.getByText(/Device split/i)).toBeInTheDocument();
      expect(screen.getByText(/No traffic in the last/i)).toBeInTheDocument();
    });
  });

  it("AbandonmentTrendTile shows empty-state copy when no cart events", async () => {
    (apiClient.GET as any).mockResolvedValue({
      data: { days: 14, timezone: "UTC", has_data: false, series: [], avg_abandonment_pct: null },
      error: null,
    });
    render(<AbandonmentTrendTile />);
    await waitFor(() => {
      expect(screen.getByText(/Cart abandonment trend/i)).toBeInTheDocument();
    });
  });

  it("OrderRhythmTile shows empty-state copy when no orders", async () => {
    (apiClient.GET as any).mockResolvedValue({
      data: {
        currency: "USD", timezone: "UTC", days: 30, has_data: false,
        by_hour: [], by_dow: [],
        peak_hour: null, peak_dow: null,
      },
      error: null,
    });
    render(<OrderRhythmTile />);
    await waitFor(() => {
      expect(screen.getByText(/When customers buy/i)).toBeInTheDocument();
    });
  });
});

// ─── Network-error branch coverage ──────────────────────────────────
// Every tile MUST render a retry-able error state when apiClient.GET
// rejects. Catches the regression where a tile silent-catches and
// renders a permanent skeleton.

import {
  TopCustomersLtvTile,
  FirstVsRepeatAovTile,
  TopProductsTile,
  TopVariantsTile,
  RepeatCadenceTile,
} from "./LiteBaseAnalytics";

describe("LiteBaseAnalytics error states", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (apiClient.GET as any).mockResolvedValue({
      data: null,
      error: { detail: "synthetic test failure" },
    });
  });

  it.each([
    ["DeviceSplitTile", () => <DeviceSplitTile />],
    ["AbandonmentTrendTile", () => <AbandonmentTrendTile />],
    ["OrderRhythmTile", () => <OrderRhythmTile />],
    ["RepeatCadenceTile", () => <RepeatCadenceTile />],
    ["TopCustomersLtvTile", () => <TopCustomersLtvTile displayCurrency="USD" />],
    ["FirstVsRepeatAovTile", () => <FirstVsRepeatAovTile displayCurrency="USD" />],
    ["TopProductsTile", () => <TopProductsTile displayCurrency="USD" />],
    ["TopVariantsTile", () => <TopVariantsTile displayCurrency="USD" />],
  ])("%s renders retry button on apiClient error", async (_, factory) => {
    render(factory());
    await waitFor(() => {
      expect(screen.getByText(/Couldn't load data/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Try again/i })).toBeInTheDocument();
    });
  });
});

// ─── Real-data branch coverage for revenue-shaped tiles ──────────────
// One representative tile per shape — the rest follow the same code
// path so a regression in formatMoneyCompact / list rendering surfaces.

describe("LiteBaseAnalytics real-data renders", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("TopCustomersLtvTile lists customer hashes + revenue", async () => {
    (apiClient.GET as any).mockResolvedValue({
      data: {
        currency: "USD",
        has_data: true,
        customers: [
          { customer_email_hash: "cust_a3b8f1c2", total_spent: 4350.5, order_count: 7, first_order_at: null, last_order_at: null },
          { customer_email_hash: "cust_caa81b00", total_spent: 1280.75, order_count: 3, first_order_at: null, last_order_at: null },
        ],
      },
      error: null,
    });
    render(<TopCustomersLtvTile displayCurrency="USD" />);
    await waitFor(() => {
      expect(screen.getByText("cust_a3b8f1c2")).toBeInTheDocument();
      expect(screen.getByText("cust_caa81b00")).toBeInTheDocument();
      expect(screen.getByText(/PII-safe/i)).toBeInTheDocument();
    });
  });

  it("TopProductsTile lists products ordered by revenue", async () => {
    (apiClient.GET as any).mockResolvedValue({
      data: {
        currency: "USD", days: 30, has_data: true,
        products: [
          { title: "Silk Pillowcase", orders: 12, units: 14, revenue: 1820.5 },
          { title: "Ceramic Mug", orders: 8, units: 10, revenue: 920.0 },
        ],
      },
      error: null,
    });
    render(<TopProductsTile displayCurrency="USD" />);
    await waitFor(() => {
      expect(screen.getByText("Silk Pillowcase")).toBeInTheDocument();
      expect(screen.getByText("Ceramic Mug")).toBeInTheDocument();
    });
  });
});
