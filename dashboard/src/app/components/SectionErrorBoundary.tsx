"use client";

import React from "react";
import { reportFrontendError } from "../lib/error-reporter";

/**
 * SectionErrorBoundary — compact per-section boundary.
 *
 * The dashboard already has a root-level DashboardErrorBoundary that
 * prevents a single render crash from white-screening the app. That
 * wrap is too coarse: one bad card in "Signals" would blank the entire
 * dashboard, hiding Brief, Overview, Settings, everything.
 *
 * This boundary is scoped to a single section. Render a small recovery
 * card in place, report to the self-healing pipeline, offer retry that
 * remounts the subtree via key-bump.
 */
export class SectionErrorBoundary extends React.Component<
  { name: string; children: React.ReactNode },
  { hasError: boolean; error: Error | null; attempt: number }
> {
  constructor(props: { name: string; children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false, error: null, attempt: 0 };
  }

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    reportFrontendError({
      component: `Section(${this.props.name})`,
      error_type: error.name || "SectionRenderError",
      message: error.message || "section render failed",
      stack: error.stack || null,
      severity: "warning",
      extra: { componentStack: (info.componentStack || "").slice(0, 2048) },
    });
  }

  private retry = () => {
    this.setState((s) => ({ hasError: false, error: null, attempt: s.attempt + 1 }));
  };

  render() {
    if (this.state.hasError) {
      return (
        <div
          role="alert"
          className="my-4 rounded-2xl border border-rose-400/20 bg-rose-500/[0.04] p-5"
        >
          <div className="mb-1 flex items-center gap-2">
            <span aria-hidden className="text-rose-300">•</span>
            <div className="text-[11px] font-bold uppercase tracking-[0.16em] text-rose-300">
              Section unavailable — {this.props.name}
            </div>
          </div>
          <p className="text-[12px] leading-relaxed text-slate-400">
            This section hit a display error. Your data is safe; the rest of
            the dashboard keeps working.
          </p>
          {this.state.error && (
            <p className="mt-2 rounded bg-black/30 p-2 text-[10px] text-rose-300/60 font-mono break-all">
              {this.state.error.message}
            </p>
          )}
          <button
            type="button"
            onClick={this.retry}
            className="mt-3 rounded-lg border border-rose-300/30 bg-rose-500/10 px-3 py-1.5 text-[11px] font-semibold text-rose-200 transition hover:bg-rose-500/20 focus:outline-none focus:ring-2 focus:ring-rose-300/50"
          >
            Retry section
          </button>
        </div>
      );
    }
    return (
      <React.Fragment key={this.state.attempt}>{this.props.children}</React.Fragment>
    );
  }
}
