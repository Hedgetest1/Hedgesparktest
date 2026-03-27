"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Image from "next/image";
import {
  type SparkNotification,
  type NotificationSettings,
  loadSettings,
  saveSettings,
} from "../lib/sparkNotifications";

// ---------------------------------------------------------------------------
// Toast — auto-dismissing notification
// ---------------------------------------------------------------------------
export function SparkToast({
  notification,
  onDismiss,
  onNavigate,
}: {
  notification: SparkNotification;
  onDismiss: () => void;
  onNavigate?: (section: string) => void;
}) {
  useEffect(() => {
    const timer = setTimeout(onDismiss, 8000);
    return () => clearTimeout(timer);
  }, [onDismiss]);

  const typeColor = {
    proof: "border-emerald-400/20 bg-emerald-500/[0.07]",
    critical: "border-rose-400/20 bg-rose-500/[0.07]",
    pattern: "border-violet-400/20 bg-violet-500/[0.07]",
  }[notification.type];

  const dotColor = {
    proof: "bg-emerald-400",
    critical: "bg-rose-400",
    pattern: "bg-violet-400",
  }[notification.type];

  return (
    <div
      className={`hs-fade-up flex items-start gap-3 rounded-xl border px-4 py-3 shadow-lg backdrop-blur-sm ${typeColor} ${
        notification.target && onNavigate ? "cursor-pointer" : ""
      }`}
      onClick={notification.target && onNavigate ? () => onNavigate(notification.target!) : undefined}
    >
      <Image
        src="/branding/hedgespark-mascot.png"
        alt=""
        width={20}
        height={20}
        className="mt-0.5 flex-shrink-0"
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className={`h-1.5 w-1.5 flex-shrink-0 rounded-full ${dotColor}`} />
          <span className="text-[12px] font-medium text-white">{notification.message}</span>
        </div>
        {notification.detail && (
          <span className="mt-0.5 block text-[11px] text-slate-400">{notification.detail}</span>
        )}
      </div>
      <button
        onClick={(e) => { e.stopPropagation(); onDismiss(); }}
        className="flex-shrink-0 rounded p-0.5 text-slate-600 transition hover:text-slate-400"
      >
        <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Bell + dropdown
// ---------------------------------------------------------------------------
export function NotificationBell({
  notifications,
  isProUser,
}: {
  notifications: SparkNotification[];
  isProUser: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [settings, setSettings] = useState<NotificationSettings>(loadSettings);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    function handler(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const toggle = useCallback((key: keyof NotificationSettings) => {
    setSettings(prev => {
      const next = { ...prev, [key]: !prev[key] };
      saveSettings(next);
      return next;
    });
  }, []);

  const hasUnread = notifications.length > 0;

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setOpen(o => !o)}
        className={`relative flex items-center justify-center rounded-full p-2 transition-colors ${
          settings.enabled
            ? "text-slate-300 hover:bg-white/[0.05]"
            : "text-slate-600 hover:bg-white/[0.03] hover:text-slate-500"
        }`}
        aria-label="Notifications"
      >
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4">
          <path strokeLinecap="round" strokeLinejoin="round" d="M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75v-.7V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0" />
        </svg>
        {hasUnread && settings.enabled && (
          <span className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-violet-400" />
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-full z-50 mt-2 w-72 overflow-hidden rounded-xl border border-white/[0.08] bg-[#0d0d1e] shadow-[0_8px_32px_rgba(0,0,0,0.4)]">
          {/* Header */}
          <div className="flex items-center justify-between border-b border-white/[0.06] px-4 py-2.5">
            <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
              Spark Alerts
            </span>
            <button
              onClick={() => toggle("enabled")}
              className={`rounded-full px-2.5 py-0.5 text-[10px] font-semibold transition ${
                settings.enabled
                  ? "bg-violet-500/20 text-violet-300"
                  : "bg-white/[0.04] text-slate-600"
              }`}
            >
              {settings.enabled ? "On" : "Off"}
            </button>
          </div>

          {!settings.enabled ? (
            <div className="px-4 py-5 text-center">
              <p className="text-[12px] text-slate-500">
                Enable alerts to get notified when Spark detects something important.
              </p>
              <button
                onClick={() => toggle("enabled")}
                className="mt-3 rounded-lg bg-violet-500/15 px-4 py-1.5 text-[11px] font-semibold text-violet-300 transition hover:bg-violet-500/25"
              >
                Enable Spark Alerts
              </button>
            </div>
          ) : (
            <>
              {/* Recent notifications */}
              {notifications.length > 0 ? (
                <div className="max-h-48 overflow-y-auto">
                  {notifications.map(n => (
                    <div key={n.id} className="flex items-start gap-2.5 border-b border-white/[0.04] px-4 py-2.5 last:border-0">
                      <span className={`mt-1 h-1.5 w-1.5 flex-shrink-0 rounded-full ${
                        n.type === "proof" ? "bg-emerald-400"
                        : n.type === "critical" ? "bg-rose-400"
                        : "bg-violet-400"
                      }`} />
                      <div className="min-w-0">
                        <p className="text-[11px] leading-[1.5] text-slate-300">{n.message}</p>
                        {n.detail && <p className="text-[10px] text-slate-600">{n.detail}</p>}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="px-4 py-4 text-center">
                  <p className="text-[11px] text-slate-600">No alerts yet. Spark will notify you when something important changes.</p>
                </div>
              )}

              {/* Settings — Pro gets granular control, Lite gets simple on/off */}
              {isProUser && (
                <div className="border-t border-white/[0.06] px-4 py-2.5">
                  <div className="text-[9px] font-semibold uppercase tracking-[0.12em] text-slate-700 mb-2">Alert types</div>
                  {([
                    { key: "proofUpdates" as const, label: "Improvements detected" },
                    { key: "criticalActions" as const, label: "Critical revenue loss" },
                    { key: "patterns" as const, label: "Store-wide patterns" },
                  ]).map(({ key, label }) => (
                    <button
                      key={key}
                      onClick={() => toggle(key)}
                      className="flex w-full items-center justify-between py-1"
                    >
                      <span className="text-[11px] text-slate-400">{label}</span>
                      <span className={`h-3 w-6 rounded-full transition ${
                        settings[key] ? "bg-violet-500/40" : "bg-white/[0.06]"
                      }`}>
                        <span className={`block h-3 w-3 rounded-full transition-transform ${
                          settings[key] ? "translate-x-3 bg-violet-400" : "translate-x-0 bg-slate-600"
                        }`} />
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
