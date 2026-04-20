/**
 * SectionHeading — unified section heading for the entire dashboard.
 *
 * Design system (founder directive 2026-04-20, "lavora meglio come
 * visual design"):
 *
 *   eyebrow  → 10px bold uppercase tracking-[0.18em] amber `#e8a04e`
 *   title    → 22px extrabold leading-tight WHITE (NOT amber — amber
 *              is reserved for the eyebrow accent only; repeating
 *              amber on both eyebrow and title is what made the old
 *              headers feel visually mush)
 *   subtitle → 13px leading-relaxed slate-400 — NEVER amber
 *
 * For hero moments (first-of-page, system-status bar), use
 * `variant="hero"` which bumps the title to 1.75rem → 2rem but keeps
 * the eyebrow + subtitle treatment identical. Two sizes, one system.
 *
 * What NOT to do:
 *   - Don't color the title amber.
 *   - Don't color the subtitle amber.
 *   - Don't invent new eyebrow sizes per section.
 *   - Don't wrap the eyebrow in a pill/chip — the color already
 *     carries the "eyebrow" signal.
 *
 * Prior API contract (pre 2026-04-20): `eyebrow` rendered as the BIG
 * amber heading and `title` rendered as a smaller slate subtitle.
 * That API was backwards — "eyebrow" should BE the eyebrow, not the
 * title. The rewrite inverts the rendering to match the names:
 * eyebrow = eyebrow, title = title. Every existing call-site stays
 * compatible because the prop NAMES didn't change — only what gets
 * rendered where.
 */

export function SectionHeading({
  eyebrow,
  title,
  description,
  variant = "section",
}: {
  eyebrow: string;
  title: string;
  description?: string;
  variant?: "section" | "hero";
  /**
   * @deprecated The "PRO" badge next to section titles was visual
   * noise: Pro users already know they're Pro, and Lite users see
   * the ProGate overlay which already labels the section as Pro.
   * The prop is still accepted at callsites (no-op) to avoid churn.
   */
  pro?: boolean;
}) {
  const titleClass =
    variant === "hero"
      ? "text-[1.75rem] font-extrabold leading-[1.08] tracking-tight text-white sm:text-[2rem]"
      : "text-[22px] font-extrabold leading-[1.15] tracking-tight text-white sm:text-[24px]";

  return (
    <div className="mb-5">
      <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.18em] text-[#e8a04e]">
        {eyebrow}
      </div>
      <h2 className={titleClass}>{title}</h2>
      {description && (
        <p className="mt-2 max-w-2xl text-[13px] leading-relaxed text-slate-400">
          {description}
        </p>
      )}
    </div>
  );
}
