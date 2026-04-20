"use client";

/**
 * PreviewBanner — sticky top banner shown when the current URL has
 * `?as=starter` or `?as=lite` (preview mode).
 *
 * Rendered identically on every floor route (Pulse, Intelligence,
 * Operations). Lives in one place so the copy and exit-button
 * behavior can't drift across pages.
 *
 * Exits by stripping the `as` query param and reloading. We use a
 * full reload instead of client-side router.replace because the
 * tier resolution happens once-per-mount (via useSession) — a
 * reload is the cleanest way to re-resolve with the real plan.
 */

export function PreviewBanner({ isPreviewing }: { isPreviewing: boolean }) {
  if (!isPreviewing) return null;
  return (
    <div
      className="fixed inset-x-0 top-0 z-[9999] flex items-center justify-center gap-3 bg-[#e8a04e] px-4 py-2 text-[13px] font-bold text-[#0b1220] shadow-[0_4px_20px_-4px_rgba(232,160,78,0.5)]"
      role="status"
      aria-live="polite"
    >
      <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-[#0b1220]" />
      Previewing as Lite — you are seeing the entry-tier experience
      <button
        type="button"
        onClick={() => {
          const url = new URL(window.location.href);
          url.searchParams.delete("as");
          window.location.href = url.toString();
        }}
        className="ml-2 rounded-md border border-[#0b1220]/40 bg-[#0b1220]/10 px-3 py-0.5 text-[12px] font-bold uppercase tracking-[0.1em] transition-colors hover:bg-[#0b1220]/20"
      >
        Exit preview
      </button>
    </div>
  );
}
