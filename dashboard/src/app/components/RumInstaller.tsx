"use client";

/**
 * RumInstaller — zero-UI client component that bootstraps the
 * real-user web-vitals collector on mount. Mirrors the
 * ErrorReporterInstaller pattern so we avoid wrapping the tree in
 * an extra boundary. Idempotent: reinstalls are no-ops.
 *
 * Mounted from app/layout.tsx so every route — app, landing,
 * pricing, proof — ships vitals.
 */

import { useEffect } from "react";
import { installRumCollector } from "../lib/rum";

export function RumInstaller(): null {
  useEffect(() => {
    installRumCollector();
  }, []);
  return null;
}
