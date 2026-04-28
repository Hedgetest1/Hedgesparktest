/**
 * Reports Hub render tests — Gap #1.
 *
 * Covers:
 *   - Standard reports section renders 6 prebuilt tiles
 *   - Custom reports section shows empty-state when no reports
 *   - Custom reports section lists saved reports when present
 *   - "+ New report" CTA links to /app/reports/new
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

// Mock Next.js navigation hooks
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

// Mock FloorLayout to render the children directly without auth chrome
vi.mock("../../../components/FloorLayout", () => ({
  FloorLayout: ({ children }: { children: (s: unknown) => React.ReactNode }) =>
    children({ shop: "test.myshopify.com", tier: "lite", isProUser: false, resolved: true }),
}));

vi.mock("../../../components/ExportButton", () => ({
  ExportButton: ({ surface, format }: { surface: string; format: string }) => (
    <span data-testid={`export-${surface}-${format}`}>Export {format.toUpperCase()}</span>
  ),
}));

const apiGet = vi.fn();
vi.mock("../../../lib/api-client", () => ({
  apiClient: { GET: (...args: unknown[]) => apiGet(...args) },
}));

import ReportsHubPage from "../page";

beforeEach(() => {
  apiGet.mockReset();
});
afterEach(() => {
  vi.clearAllMocks();
});

const STANDARD = [
  { surface: "rars", title: "Revenue at Risk", description: "Where money is leaking right now and what's recoverable." },
  { surface: "benchmarks", title: "Peer benchmarks", description: "How your store compares to similar-sized peers." },
  { surface: "benchmarks_vertical", title: "Vertical benchmarks", description: "Same comparison, narrowed to your category." },
  { surface: "pnl", title: "P&L waterfall", description: "Last 30 days of revenue, costs, and what's left." },
  { surface: "cohorts_monthly", title: "Monthly cohorts", description: "How each month's customers behave over time." },
  { surface: "attribution", title: "Channel attribution", description: "Where your converting traffic actually comes from." },
];

describe("ReportsHubPage", () => {
  it("renders the 6 prebuilt reports + empty-state custom section", async () => {
    apiGet.mockImplementation((path: string) => {
      if (path === "/merchant/reports/standard") {
        return Promise.resolve({ data: { surfaces: STANDARD }, error: null });
      }
      if (path === "/merchant/reports") {
        return Promise.resolve({ data: { reports: [], total: 0 }, error: null });
      }
      return Promise.resolve({ data: null, error: { detail: "unknown path" } });
    });

    render(<ReportsHubPage />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1, name: /^Reports$/ })).toBeInTheDocument();
      expect(screen.getByText("Prebuilt reports")).toBeInTheDocument();
    });
    // 6 prebuilt titles render
    for (const surface of STANDARD) {
      expect(screen.getByText(surface.title)).toBeInTheDocument();
    }
    // Empty-state copy for the Custom Reports section
    expect(screen.getByText(/Build your first custom report/i)).toBeInTheDocument();
    // "+ New report" CTA visible
    expect(screen.getByRole("link", { name: /\+ New report/i })).toBeInTheDocument();
  });

  it("lists saved custom reports when present", async () => {
    apiGet.mockImplementation((path: string) => {
      if (path === "/merchant/reports/standard") {
        return Promise.resolve({ data: { surfaces: STANDARD }, error: null });
      }
      if (path === "/merchant/reports") {
        return Promise.resolve({
          data: {
            reports: [
              {
                id: 42,
                name: "Revenue by channel",
                metric: "revenue",
                dimensions: ["channel"],
                date_range_preset: "last_30_days",
                scheduled: true,
                scheduled_cadence: "daily",
                last_run_at: "2026-04-28T08:00:00Z",
                updated_at: "2026-04-28T08:00:00Z",
              },
            ],
            total: 1,
          },
          error: null,
        });
      }
      return Promise.resolve({ data: null, error: { detail: "unknown" } });
    });

    render(<ReportsHubPage />);
    await waitFor(() => {
      expect(screen.getByText("Revenue by channel")).toBeInTheDocument();
    });
    expect(screen.getByText(/scheduled daily/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /View/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Edit/i })).toBeInTheDocument();
  });
});
