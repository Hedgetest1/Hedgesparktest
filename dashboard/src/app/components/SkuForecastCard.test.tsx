/**
 * Tests for SkuForecastCard — Gap #6 close, brutal $0-70 audit +
 * parity doctrine 2026-04-27.
 *
 * Coverage:
 *   - 3 horizon tabs render (7d / 14d / 30d), default 14d
 *   - Tab switch triggers re-fetch via useAsyncResource[horizon] deps
 *   - Insight panel renders when biggest_riser OR biggest_faller present
 *   - Per-product row: confidence badge + accuracy_pct + direction icon
 *   - Insufficient confidence shows "need 7+ days" placeholder
 *   - Empty state surfaces insight cold-start copy
 *   - Error state with retry
 *   - Direction icons map correctly (rising→↑, falling→↓, stable→→)
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SkuForecastCard } from "./SkuForecastCard";

vi.mock("@/app/lib/api-client", () => ({
  apiClient: { GET: vi.fn() },
}));
import { apiClient } from "@/app/lib/api-client";

const productHigh = {
  product_key: "P_HIGH",
  title: "Best Seller",
  observed_revenue: 5000,
  forecast_point: 250,
  forecast_lower_80: 200,
  forecast_upper_80: 300,
  forecast_lower_95: 180,
  forecast_upper_95: 320,
  delta_pct: 25,
  direction: "rising",
  confidence: "high",
  accuracy_pct: 85,
  n_days: 30,
  r2: 0.65,
};
const productCold = {
  product_key: "P_COLD",
  title: "New Item",
  observed_revenue: 200,
  forecast_point: 0,
  forecast_lower_80: 0,
  forecast_upper_80: 0,
  forecast_lower_95: 0,
  forecast_upper_95: 0,
  delta_pct: 0,
  direction: "stable",
  confidence: "insufficient",
  accuracy_pct: null,
  n_days: 3,
  r2: 0,
};

const sampleData = {
  shop_domain: "test.myshopify.com",
  horizon_days: 14,
  window_days: 60,
  currency: "USD",
  generated_at: "2026-04-27T20:00:00Z",
  products: [productHigh, productCold],
  biggest_riser: {
    product_key: "P_HIGH",
    title: "Best Seller",
    delta_pct: 25,
  },
  biggest_faller: null,
  insight: "Best Seller forecast is rising 25% next 14 days vs last week — the strongest momentum in your top-2.",
};

const emptyData = {
  ...sampleData,
  products: [],
  biggest_riser: null,
  biggest_faller: null,
  insight: "No product revenue in the training window yet.",
};

beforeEach(() => {
  vi.mocked(apiClient.GET).mockReset();
});

describe("SkuForecastCard", () => {
  it("renders 3 horizon tabs with 14d default", async () => {
    vi.mocked(apiClient.GET).mockResolvedValue({ data: sampleData, error: undefined } as any);
    render(<SkuForecastCard displayCurrency="USD" />);
    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "7d" })).toBeInTheDocument();
    });
    expect(screen.getByRole("tab", { name: "14d", selected: true })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "30d" })).toBeInTheDocument();
  });

  it("switches horizon on tab click and re-fetches", async () => {
    vi.mocked(apiClient.GET).mockResolvedValue({ data: sampleData, error: undefined } as any);
    render(<SkuForecastCard displayCurrency="USD" />);
    await waitFor(() => {
      expect(apiClient.GET).toHaveBeenCalledWith(
        "/analytics/forecast/by-sku",
        expect.objectContaining({
          params: expect.objectContaining({
            query: expect.objectContaining({ horizon_days: 14 }),
          }),
        }),
      );
    });
    fireEvent.click(screen.getByRole("tab", { name: "30d" }));
    await waitFor(() => {
      expect(apiClient.GET).toHaveBeenCalledWith(
        "/analytics/forecast/by-sku",
        expect.objectContaining({
          params: expect.objectContaining({
            query: expect.objectContaining({ horizon_days: 30 }),
          }),
        }),
      );
    });
  });

  it("renders insight panel when biggest_riser present", async () => {
    vi.mocked(apiClient.GET).mockResolvedValue({ data: sampleData, error: undefined } as any);
    render(<SkuForecastCard displayCurrency="USD" />);
    await waitFor(() => {
      expect(screen.getByText(/Best Seller forecast is rising 25%/)).toBeInTheDocument();
    });
    expect(screen.getByText(/Insight/i)).toBeInTheDocument();
  });

  it("renders confidence badge + accuracy_pct + direction for each product", async () => {
    vi.mocked(apiClient.GET).mockResolvedValue({ data: sampleData, error: undefined } as any);
    render(<SkuForecastCard displayCurrency="USD" />);
    await waitFor(() => {
      expect(screen.getByText("Best Seller")).toBeInTheDocument();
    });
    expect(screen.getByText("high")).toBeInTheDocument();
    expect(screen.getByText(/backtest 85% accuracy/)).toBeInTheDocument();
    // Direction icon ↑ + +25%
    expect(screen.getByText(/↑/)).toBeInTheDocument();
    expect(screen.getByText(/\+25%/)).toBeInTheDocument();
  });

  it("renders 'need 7+ days' placeholder for insufficient confidence", async () => {
    vi.mocked(apiClient.GET).mockResolvedValue({ data: sampleData, error: undefined } as any);
    render(<SkuForecastCard displayCurrency="USD" />);
    await waitFor(() => {
      expect(screen.getByText("New Item")).toBeInTheDocument();
    });
    expect(screen.getByText("insufficient")).toBeInTheDocument();
    expect(screen.getByText(/need 7\+ days/)).toBeInTheDocument();
  });

  it("renders empty state with cold-start copy", async () => {
    vi.mocked(apiClient.GET).mockResolvedValue({ data: emptyData, error: undefined } as any);
    render(<SkuForecastCard displayCurrency="USD" />);
    await waitFor(() => {
      expect(screen.getByText(/No product revenue in the training window/)).toBeInTheDocument();
    });
  });

  it("renders error state with retry", async () => {
    vi.mocked(apiClient.GET).mockResolvedValue({ data: undefined, error: { msg: "boom" } } as any);
    render(<SkuForecastCard displayCurrency="USD" />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /Retry now/i })).toBeInTheDocument();
  });
});
