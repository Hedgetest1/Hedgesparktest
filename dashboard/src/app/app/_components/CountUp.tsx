"use client";

/**
 * CountUp — animated integer tweener used throughout the dashboard KPIs.
 *
 * Extracted from app/page.tsx as the first step of the Phase Ω⁶ split
 * (see _components/README.md). Keep this component tiny and free of
 * app-specific imports — it's a pure UI primitive.
 */

import { useEffect, useRef, useState } from "react";

export function CountUp({
  value,
  format = (v: number) => v.toLocaleString(),
}: {
  value: number;
  format?: (v: number) => string;
}) {
  const [display, setDisplay] = useState(0);
  const prevRef = useRef(0);
  const rafRef = useRef<number>(0);

  useEffect(() => {
    const start = prevRef.current;
    prevRef.current = value;
    if (start === value) return;

    const began = Date.now();
    const duration = 520;

    function step() {
      const progress = Math.min((Date.now() - began) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      setDisplay(Math.round(start + (value - start) * eased));
      if (progress < 1) rafRef.current = requestAnimationFrame(step);
    }
    rafRef.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(rafRef.current);
  }, [value]);

  return <>{format(display)}</>;
}
