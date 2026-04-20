/**
 * SectionHeading — unified section heading.
 *
 * Design system (founder-corrected 2026-04-20 after a botched pass
 * that shrank titles and added redundant small-amber eyebrows):
 *
 *   Rule 1: ONE amber element per section.
 *           The big 1.75rem → 2rem amber H2 IS the section eyebrow
 *           AND the section title, in a single line. Don't add a
 *           smaller amber tag above it — that's what "amber ripetuti
 *           tra titoletto e sub titoletto" referred to.
 *
 *   Rule 2: Subtitles are slate-400, NEVER amber.
 *           Amber only appears on the main title.
 *
 *   Rule 3: Gradient (hs-brand-gradient) is reserved for the
 *           HedgeSpark wordmark. NEVER on section titles (§4 CLAUDE.md).
 *
 * API:
 *   - `eyebrow` prop renders as the BIG amber H2 (naming is
 *     historical; the prop IS the title).
 *   - `title` prop renders as the slate-400 subtitle under the H2.
 *   - `description` prop renders as the slate-500 secondary line.
 *
 * Prop names kept for backwards compat with every call-site.
 */

export function SectionHeading({
  eyebrow,
  title,
  description,
}: {
  eyebrow: string;
  title: string;
  description?: string;
  /**
   * @deprecated The "PRO" badge next to section titles was visual
   * noise: Pro users already know they're Pro, and Lite users see
   * the ProGate overlay which already labels the section as Pro.
   * The prop is still accepted at callsites (no-op).
   */
  pro?: boolean;
}) {
  return (
    <div className="mb-6">
      <h2 className="text-[1.75rem] font-extrabold leading-[1.08] tracking-tight text-[#e8a04e] sm:text-[2rem]">
        {eyebrow}
      </h2>
      {title && (
        <p className="mt-2 max-w-2xl text-[15px] leading-relaxed text-slate-400">
          {title}
        </p>
      )}
      {description && (
        <p className="mt-1 text-[13px] leading-relaxed text-slate-500">
          {description}
        </p>
      )}
    </div>
  );
}
