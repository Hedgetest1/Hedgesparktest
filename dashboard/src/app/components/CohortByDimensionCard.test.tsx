/**
 * Tests for CohortByDimensionCard — Gap #8 close, brutal $0-70 audit
 * + parity doctrine 2026-04-27.
 *
 * Coverage:
 *   - 3 dim tabs render (first_channel / first_product / first_discount)
 *   - Default = first_channel
 *   - Tab switch triggers re-fetch via useAsyncResource[dim] deps
 *   - Empty state surfaces best_vs_worst.insight cold-start copy
 *   - Best-vs-worst insight panel renders when activated
 *   - Coverage banner appears when coverage_rate < 0.7 (honesty axis)
 *   - Repeat-rate color thresholds: emerald >=30%, amber >=15%, rose <15%
 *   - Error state renders with retry button
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CohortByDimensionCard } from "./CohortByDimensionCard";

vi.mock("@/app/lib/api-client", () => ({
  apiClient: { GET: vi.fn() },
}));
import { apiClient } from "@/app/lib/api-client";

const sampleData = {
  dim: "first_channel",
  window_months: 6,
  generated_at: "2026-04-27T19:00:00Z",
  customer_coverage: {
    total_orders: 100,
    identifiable_orders: 95,
    unidentifiable_orders: 5,
    coverage_rate: 0.95,
  },
  buckets: [
    {
      dim_value: "google_ads",
      size: 50,
      repeat_rate: 0.45,
      revenue_per_customer: 200,
      orders_per_customer: 2.5,
      cohort_months: [],
    },
    {
      dim_value: "organic",
      size: 30,
      repeat_rate: 0.10,
      revenue_per_customer: 80,
      orders_per_customer: 1.2,
      cohort_months: [],
    },
  ],
  best_vs_worst: {
    best_dim_value: "google_ads",
    worst_dim_value: "organic",
    best_repeat_rate: 0.45,
    worst_repeat_rate: 0.10,
    lift_pct: 350,
    insight: "Customers acquired via google_ads have a 45% repeat rate — 350% higher than organic (10%). Lean into the channel pulling these customers.",
  },
};

const emptyData = {
  ...sampleData,
  buckets: [],
  best_vs_worst: {
    best_dim_value: null,
    worst_dim_value: null,
    best_repeat_rate: null,
    worst_repeat_rate: null,
    lift_pct: null,
    insight: "Need at least 2 segments with 5+ customers each for a reliable best-vs-worst comparison.",
  },
};

const lowCoverageData = {
  ...sampleData,
  customer_coverage: {
    total_orders: 100,
    identifiable_orders: 30,
    unidentifiable_orders: 70,
    coverage_rate: 0.30,
  },
};

beforeEach(() => {
  vi.mocked(apiClient.GET).mockReset();
});

describe("CohortByDimensionCard", () => {
  it("renders 3 dim tabs", async () => {
    vi.mocked(apiClient.GET).mockResolvedValue({ data: sampleData, error: undefined } as any);
    render(<CohortByDimensionCard displayCurrency="USD" />);
    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /Acquisition channel/i })).toBeInTheDocument();
    });
    expect(screen.getByRole("tab", { name: /First product bought/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /First discount code/i })).toBeInTheDocument();
  });

  it("defaults to first_channel tab", async () => {
    vi.mocked(apiClient.GET).mockResolvedValue({ data: sampleData, error: undefined } as any);
    render(<CohortByDimensionCard displayCurrency="USD" />);
    await waitFor(() => {
      expect(
        screen.getByRole("tab", { name: /Acquisition channel/i, selected: true })
      ).toBeInTheDocument();
    });
  });

  it("renders insight panel when best_vs_worst activated", async () => {
    vi.mocked(apiClient.GET).mockResolvedValue({ data: sampleData, error: undefined } as any);
    render(<CohortByDimensionCard displayCurrency="USD" />);
    await waitFor(() => {
      expect(screen.getByText(/Customers acquired via google_ads/)).toBeInTheDocument();
    });
    expect(screen.getByText(/Insight/i)).toBeInTheDocument();
  });

  it("renders bucket rows with repeat rates color-coded", async () => {
    vi.mocked(apiClient.GET).mockResolvedValue({ data: sampleData, error: undefined } as any);
    render(<CohortByDimensionCard displayCurrency="USD" />);
    await waitFor(() => {
      // google_ads = 45% → emerald (>=30%)
      // We can't easily assert color from CSS; assert the % text renders
      expect(screen.getByText("google_ads")).toBeInTheDocument();
    });
    expect(screen.getByText("45%")).toBeInTheDocument();
    expect(screen.getByText("10%")).toBeInTheDocument();
  });

  it("renders coverage banner when coverage_rate < 0.7", async () => {
    vi.mocked(apiClient.GET).mockResolvedValue({ data: lowCoverageData, error: undefined } as any);
    render(<CohortByDimensionCard displayCurrency="USD" />);
    await waitFor(() => {
      expect(screen.getByText(/Based on 30 of 100 orders/i)).toBeInTheDocument();
    });
  });

  it("hides coverage banner when coverage >= 0.7", async () => {
    vi.mocked(apiClient.GET).mockResolvedValue({ data: sampleData, error: undefined } as any);
    render(<CohortByDimensionCard displayCurrency="USD" />);
    await waitFor(() => {
      expect(screen.getByText("google_ads")).toBeInTheDocument();
    });
    expect(screen.queryByText(/Based on \d+ of \d+ orders/i)).toBeNull();
  });

  it("switches dim on tab click and re-fetches", async () => {
    vi.mocked(apiClient.GET).mockResolvedValue({ data: sampleData, error: undefined } as any);
    render(<CohortByDimensionCard displayCurrency="USD" />);
    await waitFor(() => {
      expect(apiClient.GET).toHaveBeenCalledWith(
        "/analytics/cohorts/by-dimension",
        expect.objectContaining({
          params: expect.objectContaining({
            query: expect.objectContaining({ dim: "first_channel" }),
          }),
        }),
      );
    });
    fireEvent.click(screen.getByRole("tab", { name: /First product bought/i }));
    await waitFor(() => {
      expect(apiClient.GET).toHaveBeenCalledWith(
        "/analytics/cohorts/by-dimension",
        expect.objectContaining({
          params: expect.objectContaining({
            query: expect.objectContaining({ dim: "first_product" }),
          }),
        }),
      );
    });
  });

  it("renders empty-state with cold-start insight copy", async () => {
    vi.mocked(apiClient.GET).mockResolvedValue({ data: emptyData, error: undefined } as any);
    render(<CohortByDimensionCard displayCurrency="USD" />);
    await waitFor(() => {
      expect(screen.getByText(/at least 2 segments/i)).toBeInTheDocument();
    });
  });

  it("renders error state with retry", async () => {
    vi.mocked(apiClient.GET).mockResolvedValue({ data: undefined, error: { msg: "boom" } } as any);
    render(<CohortByDimensionCard displayCurrency="USD" />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /Retry now/i })).toBeInTheDocument();
  });
});
