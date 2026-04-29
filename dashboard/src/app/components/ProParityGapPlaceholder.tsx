"use client";

/**
 * ProParityGapPlaceholder — "Coming next" cards for the 3 Pro tier
 * parity-gap features that need to ship to fully match $60-130
 * competitor band (KPI Goals, BI/SQL access, Subscription analytics).
 *
 * Surfaces them in the dashboard with section anchors (nav targets
 * from NAV_ITEMS_PRO) + a clear "shipping next sprint" affordance,
 * so the merchant knows the Pro roadmap without us promising what
 * isn't built.
 *
 * Accent + eyebrow + title + body all data-driven so the same
 * component renders all 3 placeholders consistently.
 */

export function ProParityGapPlaceholder({
  id,
  eyebrow,
  title,
  body,
  accent,
}: {
  id: string;
  eyebrow: string;
  title: string;
  body: string;
  accent: string;
}) {
  return (
    <section
      id={`section-${id}`}
      className="relative my-8 overflow-hidden rounded-3xl border border-dashed p-7 sm:p-9"
      style={{
        borderColor: `${accent}40`,
        background: `linear-gradient(135deg, ${accent}08 0%, transparent 80%)`,
      }}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 max-w-3xl">
          <div
            className="mb-2 inline-flex items-center gap-2 text-[10.5px] font-bold uppercase tracking-[0.18em]"
            style={{ color: accent }}
          >
            <span
              className="inline-block h-1.5 w-1.5 animate-pulse rounded-full"
              style={{ background: accent }}
              aria-hidden="true"
            />
            {eyebrow} · Coming next
          </div>
          <h2
            className="text-[1.5rem] font-extrabold leading-[1.15] tracking-tight sm:text-[1.75rem]"
            style={{ color: accent }}
          >
            {title}
          </h2>
          <p className="mt-3 text-[14px] leading-relaxed text-slate-300">{body}</p>
        </div>
        <div
          className="flex-shrink-0 rounded-full border px-3 py-1.5 text-[10.5px] font-bold uppercase tracking-[0.14em]"
          style={{
            color: accent,
            borderColor: `${accent}40`,
            background: `${accent}10`,
          }}
        >
          Shipping
        </div>
      </div>
    </section>
  );
}
