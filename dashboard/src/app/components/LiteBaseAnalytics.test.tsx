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
