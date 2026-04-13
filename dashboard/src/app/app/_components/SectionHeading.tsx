/**
 * SectionHeading — standard section header with eyebrow + optional
 * title/description. Extracted from app/page.tsx (Phase Ω⁶ split).
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
   * @deprecated The "PRO" badge next to section titles was visual noise:
   * Pro users already know they're Pro, and Lite users see the ProGate
   * overlay which already labels the section as Pro. The prop is still
   * accepted at callsites (no-op) to avoid churn — clean up later.
   */
  pro?: boolean;
}) {
  return (
    <div className="mb-6">
      <h2 className="text-[1.75rem] font-extrabold tracking-tight text-[#e8a04e] sm:text-[2rem]">
        {eyebrow}
      </h2>
      {title && (
        <p className="mt-1 text-[15px] text-slate-400">{title}</p>
      )}
      {description && (
        <p className="text-[14px] text-slate-500">{description}</p>
      )}
    </div>
  );
}
