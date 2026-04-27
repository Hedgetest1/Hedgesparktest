/**
 * CustomerChurnTile.test.tsx — Phase 2 customer-level churn tile.
 *
 * Coverage:
 *   - Cold-start state: < 30 customers with 2+ orders → "need X more"
 *   - Empty / has_data:false → cold-start copy
 *   - Real data: top-N rendered, risk pills + currency formatted
 *   - Error state: retry button surfaces
 *   - PII: only hashed cust_<hex> visible, no @ in DOM
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { CustomerChurnTile } from "./CustomerChurnTile";

// Stub global fetch since useCardFetch uses raw fetch() with credentials:include
const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const RICH_PAYLOAD = {
  currency: "USD",
  has_data: true,
  customers_with_2plus: 45,
  customers_at_risk_count: 12,
  revenue_at_risk: 4521.5,
  customers: [
    {
      customer_email_hash: "cust_a3b8f1c2",
      risk_score: 92,
      risk_band: "lapsed",
      days_since_last_order: 180,
      median_days_between_orders: 30,
      overdue_factor: 6.0,
      last_order_at: "2025-10-29T12:00:00",
      predicted_lapse_at: "2025-12-13T12:00:00",
      order_count: 5,
      total_spent: 850.0,
      suggested_action: "Last-chance offer: time-bound discount on their favorite category.",
    },
    {
      customer_email_hash: "cust_b9c2d4e5",
      risk_score: 65,
      risk_band: "at_risk",
      days_since_last_order: 60,
      median_days_between_orders: 30,
      overdue_factor: 2.0,
      last_order_at: "2026-02-26T12:00:00",
      predicted_lapse_at: "2026-05-15T12:00:00",
      order_count: 3,
      total_spent: 320.0,
      suggested_action: "Win-back sequence: 'we miss you' email with a soft incentive.",
    },
  ],
};

describe("CustomerChurnTile", () => {
  it("shows cold-start copy when customers_with_2plus < 30", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        currency: "USD",
        has_data: false,
        customers_with_2plus: 12,
        customers_at_risk_count: 0,
        revenue_at_risk: 0,
        customers: [],
      }),
    });

    render(<CustomerChurnTile apiBase="http://api.test" shop="example.myshopify.com" displayCurrency="USD" />);

    await waitFor(() => {
      expect(screen.getByText(/Customers slipping away/i)).toBeInTheDocument();
      // Need 18 more (30 - 12)
      expect(screen.getByText(/Need 18 more/i)).toBeInTheDocument();
    });
  });

  it("shows transition copy when threshold met but no at-risk yet", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        currency: "USD",
        has_data: false,
        customers_with_2plus: 35,
        customers_at_risk_count: 0,
        revenue_at_risk: 0,
        customers: [],
      }),
    });

    render(<CustomerChurnTile apiBase="http://api.test" shop="example.myshopify.com" />);

    await waitFor(() => {
      expect(screen.getByText(/personal cadence/i)).toBeInTheDocument();
    });
  });

  it("renders top-N at-risk customers with risk pills and currency", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => RICH_PAYLOAD,
    });

    render(<CustomerChurnTile apiBase="http://api.test" shop="example.myshopify.com" displayCurrency="USD" />);

    await waitFor(() => {
      // Heading
      expect(screen.getByText(/Customers slipping away/i)).toBeInTheDocument();
      // Both hashed identifiers visible
      expect(screen.getByText("cust_a3b8f1c2")).toBeInTheDocument();
      expect(screen.getByText("cust_b9c2d4e5")).toBeInTheDocument();
      // Risk band pills
      expect(screen.getByText("Lapsed")).toBeInTheDocument();
      expect(screen.getByText("At risk")).toBeInTheDocument();
      // Suggested action copy
      expect(screen.getByText(/Last-chance offer/i)).toBeInTheDocument();
    });
  });

  it("never renders raw email addresses (PII contract)", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => RICH_PAYLOAD,
    });

    const { container } = render(
      <CustomerChurnTile apiBase="http://api.test" shop="example.myshopify.com" />
    );

    await waitFor(() => {
      expect(screen.getByText("cust_a3b8f1c2")).toBeInTheDocument();
    });

    // Hard PII assertion: NO @ symbol anywhere in the rendered DOM
    expect(container.textContent).not.toContain("@");
  });

  it("surfaces retry button on fetch error", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({}),
    });

    render(<CustomerChurnTile apiBase="http://api.test" shop="example.myshopify.com" />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Retry now/i })).toBeInTheDocument();
    });
  });
});
