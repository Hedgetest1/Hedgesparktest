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
import { DateRangeProvider } from "./DateRangeContext";

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
      expect(screen.getByText(/Couldn't load this tile/i)).toBeInTheDocument();
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

  it("DeviceSplitTile renders bar per device with pct", async () => {
    (apiClient.GET as any).mockResolvedValue({
      data: {
        days: 14, total_sessions: 1000, has_data: true,
        slices: [
          { device: "mobile", sessions: 600, pct: 60.0 },
          { device: "desktop", sessions: 350, pct: 35.0 },
          { device: "tablet", sessions: 50, pct: 5.0 },
        ],
      },
      error: null,
    });
    render(<DeviceSplitTile />);
    await waitFor(() => {
      expect(screen.getByText("Mobile")).toBeInTheDocument();
      expect(screen.getByText("Desktop")).toBeInTheDocument();
      expect(screen.getByText("60%")).toBeInTheDocument();
      expect(screen.getByText("1,000 sessions")).toBeInTheDocument();
    });
  });

  it("FirstVsRepeatAovTile shows AOV uplift pct", async () => {
    (apiClient.GET as any).mockResolvedValue({
      data: {
        currency: "USD", has_data: true,
        first: { customers: 100, orders: 100, revenue: 10000, aov: 100 },
        repeat: { customers: 30, orders: 30, revenue: 4500, aov: 150 },
        aov_uplift_pct: 50.0,
      },
      error: null,
    });
    render(<FirstVsRepeatAovTile displayCurrency="USD" />);
    await waitFor(() => {
      expect(screen.getByText(/First-time \(100 customers\)/i)).toBeInTheDocument();
      expect(screen.getByText(/Repeat \(30 customers\)/i)).toBeInTheDocument();
      expect(screen.getByText(/\+50% uplift/i)).toBeInTheDocument();
    });
  });

  it("RepeatCadenceTile renders median + percentile range", async () => {
    (apiClient.GET as any).mockResolvedValue({
      data: {
        has_data: true,
        customers_with_2plus: 36,
        intervals_count: 36,
        median_days: 80.0,
        p25_days: 60.0,
        p75_days: 96.0,
        mean_days: 77.4,
      },
      error: null,
    });
    render(<RepeatCadenceTile />);
    await waitFor(() => {
      expect(screen.getByText("80")).toBeInTheDocument();
      expect(screen.getByText(/days median/i)).toBeInTheDocument();
      expect(screen.getByText(/36 repeat customers/i)).toBeInTheDocument();
      expect(screen.getByText(/60 — 96 days/i)).toBeInTheDocument();
    });
  });

  it("OrderRhythmTile renders peak hour + day", async () => {
    const by_hour = Array.from({ length: 24 }, (_, h) => ({
      hour: h, orders: h === 14 ? 50 : 5, revenue: h === 14 ? 5000 : 500,
    }));
    const by_dow = [
      { dow: 0, label: "Sun", orders: 10, revenue: 1000 },
      { dow: 1, label: "Mon", orders: 20, revenue: 2000 },
      { dow: 2, label: "Tue", orders: 30, revenue: 3000 },
      { dow: 3, label: "Wed", orders: 60, revenue: 6000 },
      { dow: 4, label: "Thu", orders: 25, revenue: 2500 },
      { dow: 5, label: "Fri", orders: 35, revenue: 3500 },
      { dow: 6, label: "Sat", orders: 15, revenue: 1500 },
    ];
    (apiClient.GET as any).mockResolvedValue({
      data: {
        currency: "USD", timezone: "Europe/Rome", days: 30, has_data: true,
        by_hour, by_dow, peak_hour: 14, peak_dow: 3,
      },
      error: null,
    });
    render(<OrderRhythmTile />);
    await waitFor(() => {
      expect(screen.getByText(/Peak:/i)).toBeInTheDocument();
      expect(screen.getByText(/Wed 14:00/)).toBeInTheDocument();
    });
  });

  it("AbandonmentTrendTile renders avg pct + day series", async () => {
    const series = Array.from({ length: 7 }, (_, i) => ({
      day: `2026-04-${20 + i}`,
      cart_adds: 100,
      purchases: 70,
      abandonment_pct: 30,
    }));
    (apiClient.GET as any).mockResolvedValue({
      data: {
        days: 7, timezone: "UTC", has_data: true,
        series, avg_abandonment_pct: 30,
      },
      error: null,
    });
    render(<AbandonmentTrendTile />);
    await waitFor(() => {
      expect(screen.getByText(/30% avg/i)).toBeInTheDocument();
    });
  });

  it("TopVariantsTile renders variant badge + SKU + units", async () => {
    (apiClient.GET as any).mockResolvedValue({
      data: {
        currency: "USD", days: 30, has_data: true,
        enriched_orders: 50, total_orders_window: 100,
        variants: [
          { variant_id: "42001", product_title: "Silk Pillowcase",
            variant_title: "Ivory / Standard", sku: "SP-IV-STD",
            units: 25, revenue: 4150.5 },
          { variant_id: "42002", product_title: "Silk Pillowcase",
            variant_title: "Charcoal / King", sku: "SP-CH-KNG",
            units: 12, revenue: 2400.0 },
        ],
      },
      error: null,
    });
    render(<TopVariantsTile displayCurrency="USD" />);
    await waitFor(() => {
      expect(screen.getByText("Ivory / Standard")).toBeInTheDocument();
      expect(screen.getByText("Charcoal / King")).toBeInTheDocument();
      expect(screen.getByText(/SKU: SP-IV-STD/i)).toBeInTheDocument();
      expect(screen.getByText(/25 units/i)).toBeInTheDocument();
    });
  });
});

// ─── Class D real-data render tests ────────────────────────────────

import {
  DiscountCodesTile,
  OrderStatusTile,
  TaxBreakdownTile,
  PaymentMethodsTile,
} from "./LiteBaseAnalytics";

describe("LiteBaseAnalytics Class D real-data", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("DiscountCodesTile lists codes + coverage banner", async () => {
    (apiClient.GET as any).mockResolvedValue({
      data: {
        currency: "USD", days: 30, has_data: true,
        enriched_orders: 25, total_orders_window: 100,
        codes: [
          { code: "SUMMER10", orders: 15, total_discount: 150.0, total_revenue: 2250.0 },
          { code: "FREESHIP", orders: 10, total_discount: 50.0, total_revenue: 1500.0 },
        ],
      },
      error: null,
    });
    render(<DiscountCodesTile displayCurrency="USD" />);
    await waitFor(() => {
      expect(screen.getByText("SUMMER10")).toBeInTheDocument();
      expect(screen.getByText("FREESHIP")).toBeInTheDocument();
      expect(screen.getByText(/Based on 25 of 100 orders/i)).toBeInTheDocument();
    });
  });

  it("OrderStatusTile renders financial + fulfillment splits with live-update copy", async () => {
    (apiClient.GET as any).mockResolvedValue({
      data: {
        days: 30, has_data: true, enriched_orders: 100,
        financial: [
          { label: "paid", orders: 80, pct: 80 },
          { label: "refunded", orders: 20, pct: 20 },
        ],
        fulfillment: [
          { label: "fulfilled", orders: 70, pct: 70 },
          { label: "unfulfilled", orders: 30, pct: 30 },
        ],
      },
      error: null,
    });
    render(<OrderStatusTile />);
    await waitFor(() => {
      expect(screen.getByText("paid")).toBeInTheDocument();
      expect(screen.getByText("fulfilled")).toBeInTheDocument();
      // Verify Note-2 closure copy: live-update language, NOT pixel-time-snapshot
      expect(screen.getByText(/Updates live as refunds \+ fulfillments fire/i)).toBeInTheDocument();
    });
  });

  it("TaxBreakdownTile shows total + effective rate", async () => {
    (apiClient.GET as any).mockResolvedValue({
      data: {
        currency: "USD", days: 30, has_data: true,
        enriched_orders: 100, total_orders_window: 150,
        total_revenue: 50000, total_tax: 3700, tax_rate_pct: 8.0,
      },
      error: null,
    });
    render(<TaxBreakdownTile displayCurrency="USD" />);
    await waitFor(() => {
      expect(screen.getByText(/Tax collected/i)).toBeInTheDocument();
      expect(screen.getByText(/8%/)).toBeInTheDocument();
      expect(screen.getByText(/Based on 100 of 150/i)).toBeInTheDocument();
    });
  });

  it("PaymentMethodsTile lists gateways", async () => {
    (apiClient.GET as any).mockResolvedValue({
      data: {
        currency: "USD", days: 30, has_data: true,
        enriched_orders: 100, total_orders_window: 100,
        methods: [
          { method: "shopify_payments", orders: 60, revenue: 12000, pct: 60 },
          { method: "paypal", orders: 30, revenue: 6000, pct: 30 },
          { method: "stripe", orders: 10, revenue: 2000, pct: 10 },
        ],
      },
      error: null,
    });
    render(<PaymentMethodsTile displayCurrency="USD" />);
    await waitFor(() => {
      expect(screen.getByText("shopify payments")).toBeInTheDocument();
      expect(screen.getByText("paypal")).toBeInTheDocument();
      expect(screen.getByText("stripe")).toBeInTheDocument();
    });
  });
});

// ─── Interaction test: retry button re-fetches ────────────────────────

describe("LiteBaseAnalytics interaction", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("Retry button re-calls apiClient on click", async () => {
    const { fireEvent } = await import("@testing-library/react");
    let callCount = 0;
    (apiClient.GET as any).mockImplementation(() => {
      callCount += 1;
      if (callCount === 1) return Promise.resolve({ data: null, error: { detail: "transient" } });
      return Promise.resolve({
        data: {
          days: 14, total_sessions: 100, has_data: true,
          slices: [{ device: "mobile", sessions: 100, pct: 100.0 }],
        },
        error: null,
      });
    });

    render(<DeviceSplitTile />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Try again/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /Try again/i }));
    await waitFor(() => {
      expect(screen.getByText("Mobile")).toBeInTheDocument();
    });
    expect(callCount).toBeGreaterThanOrEqual(2);
  });
});


// ─── Phase 3B integration: tiles pass date range to backend ──────────
//
// Born 2026-04-27 from Stage C DA-loop — verify the wired tiles
// actually call apiClient with start_date+end_date params from the
// global DateRangeContext, and that a range change triggers a
// re-fetch with the new bounds.

describe("LiteBaseAnalytics range integration (Phase 3B)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    if (typeof window !== "undefined") {
      window.localStorage.clear();
      window.history.replaceState({}, "", "/");
    }
  });

  it("DeviceSplitTile passes start_date+end_date from context to apiClient", async () => {
    (apiClient.GET as any).mockResolvedValue({
      data: { days: 7, total_sessions: 0, has_data: false, slices: [] },
      error: null,
    });

    render(
      <DateRangeProvider>
        <DeviceSplitTile />
      </DateRangeProvider>
    );

    await waitFor(() => {
      expect(apiClient.GET).toHaveBeenCalled();
    });
    // The first call should include the params object with start_date+end_date.
    // Default preset = last_7_days resolves to today−6 .. today browser-local.
    const firstCall = (apiClient.GET as any).mock.calls[0];
    expect(firstCall[0]).toBe("/analytics/device-breakdown");
    const params = firstCall[1]?.params?.query;
    expect(params).toBeDefined();
    expect(params.start_date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(params.end_date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    // start <= end
    expect(params.start_date <= params.end_date).toBe(true);
  });

  it("AbandonmentTrendTile passes range from context", async () => {
    (apiClient.GET as any).mockResolvedValue({
      data: { days: 7, timezone: "UTC", has_data: false, series: [], avg_abandonment_pct: null },
      error: null,
    });

    render(
      <DateRangeProvider>
        <AbandonmentTrendTile />
      </DateRangeProvider>
    );

    await waitFor(() => {
      expect(apiClient.GET).toHaveBeenCalled();
    });
    const firstCall = (apiClient.GET as any).mock.calls[0];
    expect(firstCall[0]).toBe("/analytics/abandonment-trend");
    expect(firstCall[1]?.params?.query?.start_date).toBeDefined();
    expect(firstCall[1]?.params?.query?.end_date).toBeDefined();
  });

  it("OrderRhythmTile passes range from context", async () => {
    (apiClient.GET as any).mockResolvedValue({
      data: {
        currency: "USD", timezone: "UTC", days: 7, has_data: false,
        by_hour: [], by_dow: [], peak_hour: null, peak_dow: null,
      },
      error: null,
    });

    render(
      <DateRangeProvider>
        <OrderRhythmTile />
      </DateRangeProvider>
    );

    await waitFor(() => {
      expect(apiClient.GET).toHaveBeenCalled();
    });
    const firstCall = (apiClient.GET as any).mock.calls[0];
    expect(firstCall[0]).toBe("/analytics/order-rhythm");
    expect(firstCall[1]?.params?.query?.start_date).toBeDefined();
    expect(firstCall[1]?.params?.query?.end_date).toBeDefined();
  });
});
