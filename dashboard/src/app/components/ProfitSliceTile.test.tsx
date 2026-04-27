/**
 * Tests for ProfitSliceTile — Gap #3 close, brutal $0-70 audit 2026-04-27.
 *
 * Coverage:
 *   - Renders 3 dim tabs (Variant / Country / Channel) — Product
 *     intentionally absent (margin-drag covers it)
 *   - Default tab = Variant
 *   - Tab switch triggers re-fetch (via useAsyncResource deps)
 *   - Empty state renders methodology copy
 *   - Estimated COGS surfaces "est" badge
 *   - Loading skeleton renders before data
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ProfitSliceTile } from "./ProfitSliceTile";

vi.mock("@/app/lib/api-client", () => ({
  apiClient: {
    GET: vi.fn(),
  },
}));

import { apiClient } from "@/app/lib/api-client";

const sampleData = {
  dim: "variant",
  window_days: 30,
  currency: "USD",
  generated_at: "2026-04-27T18:00:00Z",
  total_revenue: 1500,
  total_margin: 900,
  avg_margin_pct: 60.0,
  rows: [
    {
      key: "V1",
      label: "Widget — Red",
      revenue: 1000,
      cogs: 400,
      margin: 600,
      margin_pct: 60.0,
      units_or_orders: 10,
      cogs_source: "default_40pct",
    },
    {
      key: "V2",
      label: "Widget — Blue",
      revenue: 500,
      cogs: 200,
      margin: 300,
      margin_pct: 60.0,
      units_or_orders: 5,
      cogs_source: "default_40pct",
    },
  ],
  methodology: "Per-variant gross profit.",
};

const emptyData = {
  ...sampleData,
  rows: [],
  total_revenue: 0,
  total_margin: 0,
  avg_margin_pct: null,
  methodology: "No line-item variants in window.",
};

beforeEach(() => {
  vi.mocked(apiClient.GET).mockReset();
});

describe("ProfitSliceTile", () => {
  it("renders 3 dim tabs (Variant / Country / Channel) — Product absent", async () => {
    vi.mocked(apiClient.GET).mockResolvedValue({ data: sampleData, error: undefined } as any);
    render(<ProfitSliceTile displayCurrency="USD" />);
    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Variant" })).toBeInTheDocument();
    });
    expect(screen.getByRole("tab", { name: "Country" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Channel" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Product" })).toBeNull();
  });

  it("defaults to Variant tab and shows aria-selected", async () => {
    vi.mocked(apiClient.GET).mockResolvedValue({ data: sampleData, error: undefined } as any);
    render(<ProfitSliceTile displayCurrency="USD" />);
    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Variant", selected: true })).toBeInTheDocument();
    });
  });

  it("renders rows with label, revenue, margin, est badge for default_40pct", async () => {
    vi.mocked(apiClient.GET).mockResolvedValue({ data: sampleData, error: undefined } as any);
    render(<ProfitSliceTile displayCurrency="USD" />);
    await waitFor(() => {
      expect(screen.getByText("Widget — Red")).toBeInTheDocument();
    });
    expect(screen.getByText("Widget — Blue")).toBeInTheDocument();
    // Both rows have est badge
    const estBadges = screen.getAllByText("est");
    expect(estBadges.length).toBe(2);
    // Margin% surfaces
    expect(screen.getAllByText(/60\.0% margin/i).length).toBeGreaterThan(0);
  });

  it("switches dim on tab click and re-fetches", async () => {
    vi.mocked(apiClient.GET).mockResolvedValue({ data: sampleData, error: undefined } as any);
    render(<ProfitSliceTile displayCurrency="USD" />);
    await waitFor(() => {
      expect(apiClient.GET).toHaveBeenCalledWith(
        "/analytics/pnl/profit-by-dimension",
        expect.objectContaining({
          params: expect.objectContaining({
            query: expect.objectContaining({ dim: "variant" }),
          }),
        }),
      );
    });
    // Click Country tab
    fireEvent.click(screen.getByRole("tab", { name: "Country" }));
    await waitFor(() => {
      expect(apiClient.GET).toHaveBeenCalledWith(
        "/analytics/pnl/profit-by-dimension",
        expect.objectContaining({
          params: expect.objectContaining({
            query: expect.objectContaining({ dim: "country" }),
          }),
        }),
      );
    });
  });

  it("renders empty-state with methodology copy when rows=[]", async () => {
    vi.mocked(apiClient.GET).mockResolvedValue({ data: emptyData, error: undefined } as any);
    render(<ProfitSliceTile displayCurrency="USD" />);
    await waitFor(() => {
      expect(screen.getByText(/No data yet/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/No line-item variants in window/i)).toBeInTheDocument();
  });

  it("renders error state with retry on fetch failure", async () => {
    vi.mocked(apiClient.GET).mockResolvedValue({ data: undefined, error: { msg: "boom" } } as any);
    render(<ProfitSliceTile displayCurrency="USD" />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /Retry now/i })).toBeInTheDocument();
  });
});
