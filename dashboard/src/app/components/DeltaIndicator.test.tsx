/**
 * Tests for DeltaIndicator — comparison-toggle delta rendering primitive.
 *
 * Coverage:
 *   - Up direction (positive delta) → emerald + ↑
 *   - Down direction (negative delta) → rose + ↓
 *   - Inverse semantics (down=good) flips colors correctly
 *   - prevValue=null/undefined → null render
 *   - prev=0 + value=0 → null render
 *   - prev=0 + value>0 → "new" badge with neutral styling
 *   - sub-threshold delta → null render
 *   - Each format renders title attr correctly
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { DeltaIndicator } from "./DeltaIndicator";

describe("DeltaIndicator", () => {
  describe("direction", () => {
    it("renders emerald + ↑ for positive delta", () => {
      const { container } = render(
        <DeltaIndicator value={120} prevValue={100} format="count" />
      );
      const span = container.querySelector("span");
      expect(span?.textContent).toMatch(/↑20%/);
      expect(span?.className).toMatch(/emerald/);
    });

    it("renders rose + ↓ for negative delta", () => {
      const { container } = render(
        <DeltaIndicator value={80} prevValue={100} format="count" />
      );
      const span = container.querySelector("span");
      expect(span?.textContent).toMatch(/↓20%/);
      expect(span?.className).toMatch(/rose/);
    });
  });

  describe("inverse semantics", () => {
    it("inverse=true: down delta → emerald (good)", () => {
      const { container } = render(
        <DeltaIndicator
          value={5} prevValue={10}
          format="pct" inverse={true}
        />
      );
      const span = container.querySelector("span");
      expect(span?.textContent).toMatch(/↓50%/);
      // Down arrow but emerald color (good outcome — abandonment dropped)
      expect(span?.className).toMatch(/emerald/);
    });

    it("inverse=true: up delta → rose (bad)", () => {
      const { container } = render(
        <DeltaIndicator
          value={15} prevValue={10}
          format="pct" inverse={true}
        />
      );
      const span = container.querySelector("span");
      expect(span?.textContent).toMatch(/↑50%/);
      expect(span?.className).toMatch(/rose/);
    });
  });

  describe("edge cases", () => {
    it("prevValue=null returns null render", () => {
      const { container } = render(
        <DeltaIndicator value={100} prevValue={null} />
      );
      expect(container.firstChild).toBeNull();
    });

    it("prevValue=undefined returns null render", () => {
      const { container } = render(
        <DeltaIndicator value={100} prevValue={undefined} />
      );
      expect(container.firstChild).toBeNull();
    });

    it("both zero → null render (no change to display)", () => {
      const { container } = render(
        <DeltaIndicator value={0} prevValue={0} />
      );
      expect(container.firstChild).toBeNull();
    });

    it("prev=0 + value>0 → 'new' badge with slate styling", () => {
      const { container } = render(
        <DeltaIndicator value={50} prevValue={0} />
      );
      const span = container.querySelector("span");
      expect(span?.textContent).toMatch(/new/);
      expect(span?.className).toMatch(/slate/);
    });

    it("sub-threshold delta (default 1%) returns null render", () => {
      const { container } = render(
        <DeltaIndicator value={100.5} prevValue={100} format="count" />
      );
      // 0.5% < threshold=1 → no badge
      expect(container.firstChild).toBeNull();
    });

    it("custom threshold suppresses larger deltas", () => {
      const { container } = render(
        <DeltaIndicator
          value={105} prevValue={100} format="count" threshold={10}
        />
      );
      // 5% delta with threshold=10 → no badge
      expect(container.firstChild).toBeNull();
    });
  });

  describe("a11y + tooltip formats", () => {
    it("aria-label declares direction + magnitude + 'vs previous period'", () => {
      render(<DeltaIndicator value={130} prevValue={100} />);
      expect(screen.getByLabelText(/Up 30% vs previous period/)).toBeTruthy();
    });

    it("aria-label for new badge", () => {
      render(<DeltaIndicator value={50} prevValue={0} />);
      expect(screen.getByLabelText(/New this period/)).toBeTruthy();
    });

    it("title attr renders pct format", () => {
      const { container } = render(
        <DeltaIndicator value={5.5} prevValue={3.2} format="pct" />
      );
      const span = container.querySelector("span[title]");
      expect(span?.getAttribute("title")).toMatch(/5\.5%.*was 3\.2%/);
    });

    it("title attr renders count format", () => {
      const { container } = render(
        <DeltaIndicator value={1234} prevValue={1000} format="count" />
      );
      const span = container.querySelector("span[title]");
      // toLocaleString varies by locale; just confirm both numbers present
      expect(span?.getAttribute("title")).toMatch(/1.?234.*was.*1.?000/);
    });
  });
});
