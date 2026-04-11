"use client";

/**
 * ErrorReporterInstaller — invisible, zero-UI client component that
 * installs window.onerror + unhandledrejection handlers at mount.
 *
 * Why not a class ErrorBoundary at layout root?
 * ---------------------------------------------
 * Wrapping the root layout in a React class ErrorBoundary forces a client
 * boundary at the top of the tree and interacts badly with pages that
 * intentionally render `null` during SSR (the landing page does this via
 * its useOAuthRedirect hook to avoid flashing marketing content to a
 * merchant mid-OAuth). The boundary treats the SSR → client rerender
 * shape change as an error and falls through to the fallback, which
 * manifests as an empty/broken landing page.
 *
 * We still need to capture global errors for the self-healing pipeline,
 * so we install the window listeners here — no wrapping of children,
 * no tree impact. Route-level React render errors are covered by
 * Next's framework-level error.tsx and global-error.tsx, which do
 * forward to the same reporter.
 */

import { useEffect } from "react";
import { installGlobalErrorReporter } from "../lib/error-reporter";

export function ErrorReporterInstaller(): null {
  useEffect(() => {
    installGlobalErrorReporter();
  }, []);
  return null;
}
