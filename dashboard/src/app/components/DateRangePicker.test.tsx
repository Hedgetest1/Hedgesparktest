/**
 * Tests for DateRangeContext + DateRangePicker.
 *
 * Coverage:
 *   - rangeFromPreset for each of the 8 presets returns expected dates
 *   - DateRangeProvider initial state from URL / localStorage / default
 *   - DateRangePicker renders 8 presets, click applies, custom input shows
 *   - Esc closes; Arrow Up/Down navigate
 *   - useDateRange outside provider returns empty queryString
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import {
  DateRangeProvider, deriveCompareBounds, rangeFromPreset, useDateRange,
} from "./DateRangeContext";
import { DateRangePicker } from "./DateRangePicker";

// Pin "today" so date math is deterministic across CI runs
const FAKE_TODAY = new Date(2026, 3, 15); // April 15, 2026 (months are 0-indexed)

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(FAKE_TODAY);
  // Ensure localStorage is empty between tests
  if (typeof window !== "undefined") {
    window.localStorage.clear();
    // Clean URL params
    window.history.replaceState({}, "", "/");
  }
});

afterEach(() => {
  vi.useRealTimers();
});


describe("rangeFromPreset", () => {
  it("today returns same start and end (today)", () => {
    const r = rangeFromPreset("today");
    expect(r.start).toBe("2026-04-15");
    expect(r.end).toBe("2026-04-15");
  });

  it("yesterday returns yesterday for both bounds", () => {
    const r = rangeFromPreset("yesterday");
    expect(r.start).toBe("2026-04-14");
    expect(r.end).toBe("2026-04-14");
  });

  it("last_7_days returns 7-day inclusive window ending today", () => {
    const r = rangeFromPreset("last_7_days");
    expect(r.start).toBe("2026-04-09"); // today − 6
    expect(r.end).toBe("2026-04-15");
  });

  it("last_30_days returns 30-day inclusive window", () => {
    const r = rangeFromPreset("last_30_days");
    expect(r.start).toBe("2026-03-17"); // April 15 − 29
    expect(r.end).toBe("2026-04-15");
  });

  it("mtd starts at first of current month", () => {
    const r = rangeFromPreset("mtd");
    expect(r.start).toBe("2026-04-01");
    expect(r.end).toBe("2026-04-15");
  });

  it("qtd starts at first of current quarter", () => {
    const r = rangeFromPreset("qtd");
    // April → Q2 starts April 1
    expect(r.start).toBe("2026-04-01");
    expect(r.end).toBe("2026-04-15");
  });

  it("ytd starts at January 1", () => {
    const r = rangeFromPreset("ytd");
    expect(r.start).toBe("2026-01-01");
    expect(r.end).toBe("2026-04-15");
  });

  it("custom uses provided start/end", () => {
    const r = rangeFromPreset("custom", "2026-02-14", "2026-02-28");
    expect(r.preset).toBe("custom");
    expect(r.start).toBe("2026-02-14");
    expect(r.end).toBe("2026-02-28");
  });
});


// Helper consumer to inspect context value in tests
function ContextProbe() {
  const {
    range, queryString, compareEnabled, compareStart, compareEnd,
  } = useDateRange();
  return (
    <div>
      <span data-testid="preset">{range.preset}</span>
      <span data-testid="start">{range.start}</span>
      <span data-testid="end">{range.end}</span>
      <span data-testid="qs">{queryString}</span>
      <span data-testid="cmp-enabled">{String(compareEnabled)}</span>
      <span data-testid="cmp-start">{compareStart ?? ""}</span>
      <span data-testid="cmp-end">{compareEnd ?? ""}</span>
    </div>
  );
}


describe("DateRangeProvider initial state", () => {
  it("defaults to last_7_days when no URL or localStorage", async () => {
    render(<DateRangeProvider><ContextProbe /></DateRangeProvider>);
    // Provider's useEffect runs after mount; advance timers/microtasks
    await act(async () => {});
    expect(screen.getByTestId("preset").textContent).toBe("last_7_days");
    expect(screen.getByTestId("start").textContent).toBe("2026-04-09");
    expect(screen.getByTestId("end").textContent).toBe("2026-04-15");
  });

  it("reads preset from URL params", async () => {
    window.history.replaceState({}, "", "/?range=last_30_days");
    render(<DateRangeProvider><ContextProbe /></DateRangeProvider>);
    await act(async () => {});
    expect(screen.getByTestId("preset").textContent).toBe("last_30_days");
  });

  it("reads custom range from URL params", async () => {
    window.history.replaceState(
      {}, "", "/?range=custom&start=2026-02-14&end=2026-02-28"
    );
    render(<DateRangeProvider><ContextProbe /></DateRangeProvider>);
    await act(async () => {});
    expect(screen.getByTestId("preset").textContent).toBe("custom");
    expect(screen.getByTestId("start").textContent).toBe("2026-02-14");
  });

  it("falls back to localStorage when URL empty", async () => {
    window.localStorage.setItem(
      "hs_date_range",
      JSON.stringify({ preset: "mtd", start: "2026-04-01", end: "2026-04-15" })
    );
    render(<DateRangeProvider><ContextProbe /></DateRangeProvider>);
    await act(async () => {});
    expect(screen.getByTestId("preset").textContent).toBe("mtd");
  });

  it("queryString is start_date=...&end_date=...", async () => {
    render(<DateRangeProvider><ContextProbe /></DateRangeProvider>);
    await act(async () => {});
    expect(screen.getByTestId("qs").textContent).toBe(
      "start_date=2026-04-09&end_date=2026-04-15"
    );
  });
});


describe("useDateRange outside provider", () => {
  it("returns empty queryString and default range without crashing", () => {
    render(<ContextProbe />);
    expect(screen.getByTestId("qs").textContent).toBe("");
    // Default preset still resolves
    expect(screen.getByTestId("preset").textContent).toBe("last_7_days");
  });
});


describe("DateRangePicker UI", () => {
  it("renders trigger with current label", async () => {
    render(<DateRangeProvider><DateRangePicker /></DateRangeProvider>);
    await act(async () => {});
    expect(screen.getByRole("combobox")).toBeInTheDocument();
    // Default preset = last_7_days → label "Last 7 days"
    expect(screen.getByText("Last 7 days")).toBeInTheDocument();
  });

  it("opens dropdown on click and shows all 8 presets", async () => {
    render(<DateRangeProvider><DateRangePicker /></DateRangeProvider>);
    await act(async () => {});
    fireEvent.click(screen.getByRole("combobox"));
    // 8 preset options
    const options = screen.getAllByRole("option");
    expect(options.length).toBe(8);
    // Spot check
    expect(screen.getByText("Today")).toBeInTheDocument();
    expect(screen.getByText("Year to date")).toBeInTheDocument();
    expect(screen.getByText("Custom range")).toBeInTheDocument();
  });

  it("selecting a preset updates the range", async () => {
    render(
      <DateRangeProvider>
        <DateRangePicker />
        <ContextProbe />
      </DateRangeProvider>
    );
    await act(async () => {});
    fireEvent.click(screen.getByRole("combobox"));
    fireEvent.click(screen.getByText("Last 30 days"));
    // The dropdown closes and range updates
    expect(screen.getByTestId("preset").textContent).toBe("last_30_days");
    expect(screen.getByTestId("start").textContent).toBe("2026-03-17");
  });

  it("Custom range preset reveals date inputs", async () => {
    render(<DateRangeProvider><DateRangePicker /></DateRangeProvider>);
    await act(async () => {});
    fireEvent.click(screen.getByRole("combobox"));
    fireEvent.click(screen.getByText("Custom range"));
    // Custom inputs become visible (Apply button + 2 date inputs)
    expect(screen.getByRole("button", { name: /Apply custom range/i })).toBeInTheDocument();
    expect(screen.getByLabelText("Start date")).toBeInTheDocument();
    expect(screen.getByLabelText("End date")).toBeInTheDocument();
  });

  it("Esc closes the dropdown", async () => {
    render(<DateRangeProvider><DateRangePicker /></DateRangeProvider>);
    await act(async () => {});
    fireEvent.click(screen.getByRole("combobox"));
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    // Dropdown removed from DOM
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("Arrow keys navigate preset focus", async () => {
    render(<DateRangeProvider><DateRangePicker /></DateRangeProvider>);
    await act(async () => {});
    fireEvent.click(screen.getByRole("combobox"));
    // Open: focus lands on the active preset (last_7_days = idx 2)
    const options = screen.getAllByRole("option");
    // Arrow down → next preset
    fireEvent.keyDown(document, { key: "ArrowDown" });
    // Focus moves to next option (last_30_days at idx 3)
    expect(options[3]).toHaveFocus();
    fireEvent.keyDown(document, { key: "ArrowUp" });
    expect(options[2]).toHaveFocus();
  });
});


// ════════════════════════════════════════════════════════════════════════
// Comparison toggle — Phase 3B residual close
// ════════════════════════════════════════════════════════════════════════

describe("deriveCompareBounds", () => {
  it("derives prior 7 days for a 7-day window", () => {
    const r = deriveCompareBounds("2026-04-09", "2026-04-15");
    expect(r).not.toBeNull();
    expect(r!.compareStart).toBe("2026-04-02");
    expect(r!.compareEnd).toBe("2026-04-08");
  });

  it("derives prior single day for a single-day range", () => {
    const r = deriveCompareBounds("2026-04-15", "2026-04-15");
    expect(r).not.toBeNull();
    expect(r!.compareStart).toBe("2026-04-14");
    expect(r!.compareEnd).toBe("2026-04-14");
  });

  it("returns null for malformed input", () => {
    const r = deriveCompareBounds("not-a-date", "2026-04-15");
    // parsing produces NaN start, span < 1 → null
    expect(r).toBeNull();
  });
});

describe("DateRangeProvider compareEnabled", () => {
  it("compareEnabled defaults to false", async () => {
    render(<DateRangeProvider><ContextProbe /></DateRangeProvider>);
    await act(async () => {});
    expect(screen.getByTestId("cmp-enabled").textContent).toBe("false");
    expect(screen.getByTestId("cmp-start").textContent).toBe("");
    expect(screen.getByTestId("cmp-end").textContent).toBe("");
  });

  it("compareEnabled=true reads from URL ?compare=1", async () => {
    window.history.replaceState({}, "", "/?compare=1");
    render(<DateRangeProvider><ContextProbe /></DateRangeProvider>);
    await act(async () => {});
    expect(screen.getByTestId("cmp-enabled").textContent).toBe("true");
    // Default last_7_days (Apr 9–15) → compare = Apr 2–8
    expect(screen.getByTestId("cmp-start").textContent).toBe("2026-04-02");
    expect(screen.getByTestId("cmp-end").textContent).toBe("2026-04-08");
  });

  it("compareEnabled=true persists to localStorage", async () => {
    window.localStorage.setItem("hs_date_range_compare", "1");
    render(<DateRangeProvider><ContextProbe /></DateRangeProvider>);
    await act(async () => {});
    expect(screen.getByTestId("cmp-enabled").textContent).toBe("true");
  });

  it("queryString includes compare params when enabled", async () => {
    window.history.replaceState({}, "", "/?compare=1");
    render(<DateRangeProvider><ContextProbe /></DateRangeProvider>);
    await act(async () => {});
    expect(screen.getByTestId("qs").textContent).toBe(
      "start_date=2026-04-09&end_date=2026-04-15"
      + "&compare_start=2026-04-02&compare_end=2026-04-08"
    );
  });

  it("queryString omits compare params when disabled", async () => {
    render(<DateRangeProvider><ContextProbe /></DateRangeProvider>);
    await act(async () => {});
    expect(screen.getByTestId("qs").textContent).toBe(
      "start_date=2026-04-09&end_date=2026-04-15"
    );
  });
});

describe("DateRangePicker compare checkbox", () => {
  it("renders 'Compare to previous period' label", async () => {
    render(<DateRangeProvider><DateRangePicker /></DateRangeProvider>);
    await act(async () => {});
    fireEvent.click(screen.getByRole("combobox"));
    expect(
      screen.getByText(/Compare to previous period/i)
    ).toBeInTheDocument();
  });

  it("checking the box toggles compareEnabled in context", async () => {
    render(
      <DateRangeProvider>
        <DateRangePicker />
        <ContextProbe />
      </DateRangeProvider>
    );
    await act(async () => {});
    fireEvent.click(screen.getByRole("combobox"));
    const checkbox = screen.getByRole("checkbox", {
      name: /Compare to previous period/i,
    });
    expect(checkbox).not.toBeChecked();
    fireEvent.click(checkbox);
    expect(checkbox).toBeChecked();
    expect(screen.getByTestId("cmp-enabled").textContent).toBe("true");
    // Compare bounds populated by auto-derive
    expect(screen.getByTestId("cmp-start").textContent).toBe("2026-04-02");
  });

  it("helper text shows derived compare range when on", async () => {
    window.history.replaceState({}, "", "/?compare=1");
    render(<DateRangeProvider><DateRangePicker /></DateRangeProvider>);
    await act(async () => {});
    fireEvent.click(screen.getByRole("combobox"));
    expect(
      screen.getByText(/vs 2026-04-02 → 2026-04-08/)
    ).toBeInTheDocument();
  });
});
