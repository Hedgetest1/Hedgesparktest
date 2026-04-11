"use client";

/**
 * ClientErrorBoundary — top-level React error boundary that forwards any
 * render-time exception to the self-healing pipeline via /ops/frontend-errors.
 *
 * Placed at the root layout so any component subtree crash is captured,
 * reported, and a graceful fallback UI is shown. The reporter is intentionally
 * tiny (see lib/error-reporter.ts) — we do NOT bundle Sentry or any external
 * SDK; the backend owns the pipeline.
 *
 * Why a class component: React error boundaries require class components for
 * the `componentDidCatch` / `getDerivedStateFromError` lifecycle. The rest of
 * the app is function components.
 */

import React from "react";
import {
  installGlobalErrorReporter,
  reportFrontendError,
} from "../lib/error-reporter";

interface Props {
  children: React.ReactNode;
}

interface State {
  hasError: boolean;
  errorMessage: string | null;
}

export class ClientErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, errorMessage: null };
  }

  componentDidMount(): void {
    // Install window.onerror and unhandledrejection handlers once per page.
    installGlobalErrorReporter();
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, errorMessage: error.message || "render error" };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo): void {
    try {
      reportFrontendError({
        component: "ClientErrorBoundary",
        error_type: error.name || "Error",
        message: error.message || "react render error",
        stack: error.stack || null,
        severity: "critical",
        extra: {
          component_stack: (info && info.componentStack) || null,
        },
      });
    } catch {
      /* never rethrow from a boundary */
    }
  }

  private handleReset = (): void => {
    this.setState({ hasError: false, errorMessage: null });
  };

  render(): React.ReactNode {
    if (this.state.hasError) {
      return (
        <div
          role="alert"
          className="flex min-h-[60vh] items-center justify-center p-8"
        >
          <div className="max-w-md rounded-2xl border border-white/[0.08] bg-[#0b0b14]/90 p-6 shadow-2xl">
            <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.18em] text-rose-400">
              Unexpected error
            </div>
            <h2 className="text-[18px] font-bold text-white">
              Something broke on this screen
            </h2>
            <p className="mt-2 text-[13px] leading-relaxed text-slate-400">
              We&apos;ve already sent a report to our team. You can retry the
              action or reload the page.
            </p>
            {this.state.errorMessage && (
              <pre className="mt-3 max-h-24 overflow-auto rounded-lg border border-white/[0.05] bg-white/[0.02] p-2 text-[10px] text-slate-500">
                {this.state.errorMessage}
              </pre>
            )}
            <div className="mt-4 flex gap-2">
              <button
                type="button"
                onClick={this.handleReset}
                className="rounded-lg border border-white/[0.1] bg-white/[0.04] px-3 py-1.5 text-[12px] font-semibold text-slate-200 transition-colors hover:border-white/[0.2] hover:bg-white/[0.08]"
              >
                Try again
              </button>
              <button
                type="button"
                onClick={() => window.location.reload()}
                className="rounded-lg border border-white/[0.1] bg-white/[0.04] px-3 py-1.5 text-[12px] font-semibold text-slate-200 transition-colors hover:border-white/[0.2] hover:bg-white/[0.08]"
              >
                Reload
              </button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
