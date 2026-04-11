"use client";

/**
 * Next.js global error boundary — the absolute last line of defense.
 *
 * Fires when the root layout itself crashes. Since the root layout is gone
 * at this point, we have to render our own <html>/<body>. We still forward
 * the error to the self-healing pipeline via POST /ops/frontend-errors.
 *
 * Reference: https://nextjs.org/docs/app/api-reference/file-conventions/error#global-errorjs
 */

import { useEffect } from "react";
import { reportFrontendError } from "./lib/error-reporter";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    reportFrontendError({
      component: "next:global-error",
      error_type: error.name || "Error",
      message: error.message || "global layout crash",
      stack: error.stack || null,
      severity: "critical",
      extra: { digest: error.digest ?? null },
    });
  }, [error]);

  return (
    <html lang="en">
      <body
        style={{
          minHeight: "100vh",
          background: "#0b0b14",
          color: "#e2e8f0",
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: "2rem",
        }}
      >
        <div
          style={{
            maxWidth: 420,
            background: "rgba(15,15,25,0.95)",
            border: "1px solid rgba(255,255,255,0.08)",
            borderRadius: 16,
            padding: 24,
          }}
        >
          <div
            style={{
              fontSize: 10,
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.18em",
              color: "#fb7185",
              marginBottom: 4,
            }}
          >
            Fatal error
          </div>
          <h2 style={{ fontSize: 18, fontWeight: 700, color: "#fff", margin: 0 }}>
            The dashboard failed to render
          </h2>
          <p style={{ fontSize: 13, color: "#94a3b8", marginTop: 8 }}>
            An automated report has been sent. Please reload.
          </p>
          <button
            type="button"
            onClick={reset}
            style={{
              marginTop: 16,
              padding: "6px 12px",
              fontSize: 12,
              fontWeight: 600,
              background: "rgba(255,255,255,0.04)",
              border: "1px solid rgba(255,255,255,0.1)",
              borderRadius: 8,
              color: "#e2e8f0",
              cursor: "pointer",
            }}
          >
            Reload
          </button>
        </div>
      </body>
    </html>
  );
}
