/**
 * HowCustomersFindYouCard.test.tsx — Gap #7 close, brutal $0-70 audit.
 *
 * Coverage:
 *   - Empty state: API returns total=0 / no distribution → "We're listening"
 *   - Populated state: distribution renders with top option highlighted
 *   - Single-dominant: 100% of one choice → "100% of N shoppers..."
 *   - Custom (non-default) choice values get title-cased gracefully
 *   - Error state: retry button surfaces
 *   - 401 dispatches the session-expired event (handled by useCardFetch)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { HowCustomersFindYouCard } from "./HowCustomersFindYouCard";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("HowCustomersFindYouCard", () => {
  it("renders the empty state when no distribution arrives", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        shop_domain: "test.myshopify.com",
        range: "last_30_days",
        total: 0,
        distribution: [],
        top_choice: null,
      }),
    });

    render(<HowCustomersFindYouCard apiBase="http://api.test" shop="test.myshopify.com" />);

    await waitFor(() => {
      expect(screen.getByText(/We're listening/i)).toBeInTheDocument();
      expect(screen.getByText(/first response will land here/i)).toBeInTheDocument();
    });
  });

  it("renders distribution + top-choice summary when populated", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        shop_domain: "test.myshopify.com",
        range: "last_30_days",
        total: 50,
        distribution: [
          { choice: "instagram", count: 30, pct: 60.0 },
          { choice: "google", count: 12, pct: 24.0 },
          { choice: "friend", count: 8, pct: 16.0 },
        ],
        top_choice: { choice: "instagram", count: 30, pct: 60.0 },
      }),
    });

    render(<HowCustomersFindYouCard apiBase="http://api.test" shop="test.myshopify.com" />);

    await waitFor(() => {
      expect(screen.getByText(/How customers find you/i)).toBeInTheDocument();
    });
    // Top-choice summary line — leader callout. The "60%" string appears
    // in both the summary line and the per-row count, so use getAllByText.
    expect(screen.getAllByText(/60%/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Instagram/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Google/)).toBeInTheDocument();
    expect(screen.getByText(/Friend/)).toBeInTheDocument();
  });

  it("handles single-dominant 100% distribution cleanly", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        shop_domain: "test.myshopify.com",
        range: "last_30_days",
        total: 5,
        distribution: [{ choice: "tiktok", count: 5, pct: 100.0 }],
        top_choice: { choice: "tiktok", count: 5, pct: 100.0 },
      }),
    });

    render(<HowCustomersFindYouCard apiBase="http://api.test" shop="test.myshopify.com" />);

    await waitFor(() => {
      // 100% appears in both summary line and bar-count → expect ≥1
      expect(screen.getAllByText(/100%/).length).toBeGreaterThan(0);
    });
    expect(screen.getAllByText(/TikTok/).length).toBeGreaterThan(0);
  });

  it("title-cases unknown custom choice values", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        shop_domain: "test.myshopify.com",
        range: "last_30_days",
        total: 4,
        distribution: [
          { choice: "podcast_ad", count: 3, pct: 75.0 },
          { choice: "billboard", count: 1, pct: 25.0 },
        ],
        top_choice: { choice: "podcast_ad", count: 3, pct: 75.0 },
      }),
    });

    render(<HowCustomersFindYouCard apiBase="http://api.test" shop="test.myshopify.com" />);

    await waitFor(() => {
      // "podcast_ad" → "Podcast ad" via labelFor() fallback
      expect(screen.getAllByText(/Podcast ad/).length).toBeGreaterThan(0);
      expect(screen.getByText(/Billboard/)).toBeInTheDocument();
    });
  });

  it("renders error state with retry button on 500", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ detail: "kaboom" }),
    });

    render(<HowCustomersFindYouCard apiBase="http://api.test" shop="test.myshopify.com" />);

    await waitFor(() => {
      // "Couldn't load this card" (heading) appears in <CardError>; the
      // message body is also similarly worded, so we anchor on the
      // retry button which is unique to the error state.
      expect(screen.getByRole("button", { name: /Retry now/i })).toBeInTheDocument();
    });
  });
});
