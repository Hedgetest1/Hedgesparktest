/**
 * StockHealthCard.test.tsx — Gap #4 Inventory KPIs.
 *
 * Coverage:
 *   - Empty state when no snapshots
 *   - Populated state with mixed in-stock + out-of-stock
 *   - Top-at-risk row renders with days-of-cover
 *   - Error state with retry button
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { StockHealthCard } from "./StockHealthCard";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("StockHealthCard", () => {
  it("renders the empty state when products_tracked=0", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        shop_domain: "x.myshopify.com",
        products_tracked: 0,
        out_of_stock_count: 0,
        low_stock_count: 0,
        days_of_cover_top: null,
        top_at_risk: [],
        headline: "We're listening — your first snapshot lands within 24h.",
        lead_time_days: 14,
        last_snapshot_at: null,
      }),
    });

    render(<StockHealthCard apiBase="http://api.test" shop="x.myshopify.com" />);
    await waitFor(() => {
      expect(screen.getByText(/We're listening/i)).toBeInTheDocument();
      expect(screen.getByText(/Daily snapshot via Shopify/i)).toBeInTheDocument();
    });
  });

  it("renders headline + counters + at-risk rows when populated", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        shop_domain: "x.myshopify.com",
        products_tracked: 10,
        out_of_stock_count: 2,
        low_stock_count: 3,
        days_of_cover_top: 4,
        top_at_risk: [
          { product_url: "/products/blue-hoodie", product_title: "Blue Hoodie", days_of_cover: 4, inventory_quantity: 8 },
          { product_url: "/products/red-mug", product_title: "Red Mug", days_of_cover: 6, inventory_quantity: 12 },
        ],
        headline: "5 SKUs need a reorder soon.",
        lead_time_days: 14,
        last_snapshot_at: "2026-04-28T08:00:00Z",
      }),
    });

    render(<StockHealthCard apiBase="http://api.test" shop="x.myshopify.com" />);
    await waitFor(() => {
      expect(screen.getByText(/Stock health/i)).toBeInTheDocument();
    });
    expect(screen.getByText("Blue Hoodie")).toBeInTheDocument();
    expect(screen.getByText("Red Mug")).toBeInTheDocument();
    // Counter values
    expect(screen.getByText("2")).toBeInTheDocument();   // out of stock
    expect(screen.getByText("3")).toBeInTheDocument();   // low stock
    expect(screen.getByText(/5 SKUs need a reorder soon/i)).toBeInTheDocument();
  });

  it("shows healthy state copy when nothing is at risk", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        shop_domain: "x.myshopify.com",
        products_tracked: 10,
        out_of_stock_count: 0,
        low_stock_count: 0,
        days_of_cover_top: null,
        top_at_risk: [],
        headline: "All products have healthy stock right now.",
        lead_time_days: 14,
        last_snapshot_at: "2026-04-28T08:00:00Z",
      }),
    });

    render(<StockHealthCard apiBase="http://api.test" shop="x.myshopify.com" />);
    // Headline + at-risk-empty banner both contain "healthy stock";
    // expect at least one (≥1) match instead of demanding uniqueness.
    await waitFor(() => {
      expect(screen.getAllByText(/healthy stock/i).length).toBeGreaterThan(0);
    });
  });

  it("renders error state with retry button on 500", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ detail: "kaboom" }),
    });

    render(<StockHealthCard apiBase="http://api.test" shop="x.myshopify.com" />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Retry now/i })).toBeInTheDocument();
    });
  });
});
