import Image from "next/image";

type SparkState = "loading" | "success" | "idle";

const GLOW: Record<SparkState, string> = {
  loading: "rgba(99, 102, 241, 0.14)",
  success: "rgba(251, 191, 36, 0.10)",
  idle:    "rgba(148, 163, 184, 0.06)",
};

/**
 * MascotLoader — full-screen centered state.
 *
 * Spark at 180px, single soft glow, gentle float.
 * Spark → caption → optional card as one tight block.
 */
export function MascotLoader({
  caption = "Loading your store data\u2026",
  state = "loading",
  children,
}: {
  caption?: string;
  state?: SparkState;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex min-h-[70vh] flex-col items-center justify-center px-6">
      <div className="flex flex-col items-center">
        {/* Spark + single glow */}
        <div className="relative">
          <div
            className="absolute inset-[-40px] rounded-full"
            style={{
              background: `radial-gradient(circle, ${GLOW[state]} 0%, transparent 70%)`,
            }}
          />
          <Image
            src="/branding/hedgespark/spark.png"
            alt=""
            width={180}
            height={180}
            className="hs-float relative"
            priority
          />
        </div>

        {/* Caption — tight to Spark */}
        <p className="mt-3 text-[15px] text-slate-400">{caption}</p>

        {/* Optional card — tight to caption */}
        {children && <div className="mt-3 w-full max-w-sm">{children}</div>}
      </div>
    </div>
  );
}

/**
 * MascotEmpty — section empty state.
 * Spark at 72px, static, single soft glow.
 */
export function MascotEmpty({
  message = "Collecting data \u2014 this section fills up as visitors arrive.",
  subtext,
}: {
  message?: string;
  subtext?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 rounded-2xl border border-dashed border-white/[0.06] bg-white/[0.015] py-14">
      <div className="relative">
        <div
          className="absolute inset-[-12px] rounded-full"
          style={{
            background: `radial-gradient(circle, ${GLOW.idle} 0%, transparent 70%)`,
          }}
        />
        <Image
          src="/branding/hedgespark/spark.png"
          alt=""
          width={72}
          height={72}
          className="relative opacity-60"
        />
      </div>
      <div className="text-center">
        <p className="text-[13px] text-slate-400">{message}</p>
        {subtext && <p className="mt-1 text-[11px] text-slate-400">{subtext}</p>}
      </div>
    </div>
  );
}
