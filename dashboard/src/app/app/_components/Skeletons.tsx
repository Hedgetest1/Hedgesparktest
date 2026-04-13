/**
 * Skeleton loading atoms — Divider, KpiSkeleton, TableSkeleton.
 * Extracted from app/page.tsx (Phase Ω⁶ split).
 */

export function Divider() {
  return <div className="border-t border-white/[0.06]" />;
}

export function KpiSkeleton() {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="animate-pulse rounded-2xl border border-white/[0.07] bg-white/[0.03] p-4">
          <div className="h-3 w-20 rounded bg-white/[0.06]" />
          <div className="mt-3 h-7 w-16 rounded bg-white/[0.06]" />
          <div className="mt-2 h-2.5 w-28 rounded bg-white/[0.04]" />
        </div>
      ))}
    </div>
  );
}

export function TableSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="animate-pulse overflow-hidden rounded-2xl border border-white/[0.07] bg-white/[0.02]">
      <div className="border-b border-white/[0.06] px-4 py-3">
        <div className="h-3 w-32 rounded bg-white/[0.06]" />
      </div>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center gap-4 border-t border-white/[0.04] px-4 py-3">
          <div className="h-2 w-2 rounded-full bg-white/[0.06]" />
          <div className="h-3 w-40 rounded bg-white/[0.06]" />
          <div className="ml-auto h-3 w-12 rounded bg-white/[0.04]" />
          <div className="h-3 w-16 rounded bg-white/[0.04]" />
        </div>
      ))}
    </div>
  );
}
