import Image from "next/image";

export function MascotLoader({
  caption = "Reading the signals…",
}: {
  caption?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-5 py-20">
      <div className="relative">
        <Image
          src="/branding/hedgespark-mascot.png"
          alt="Loading"
          width={128}
          height={128}
          className="hs-bob"
          priority
        />
        <span className="hs-sparkle absolute -right-1 -top-1 text-xl leading-none text-amber-300">
          ✦
        </span>
        <span className="hs-sparkle absolute -left-2 top-6 text-sm leading-none text-violet-300" style={{ animationDelay: "0.8s" }}>
          ✦
        </span>
      </div>
      {/* Speech bubble */}
      <div className="relative rounded-2xl border border-white/[0.08] bg-white/[0.03] px-5 py-3">
        <div className="absolute -top-2 left-1/2 h-3 w-3 -translate-x-1/2 rotate-45 border-l border-t border-white/[0.08] bg-white/[0.03]" />
        <p className="text-[13px] text-slate-400">{caption}</p>
      </div>
    </div>
  );
}

export function MascotEmpty({
  message = "No sparks yet — check back soon.",
}: {
  message?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 rounded-2xl border border-dashed border-white/[0.08] bg-white/[0.02] py-12">
      <div className="relative">
        <Image
          src="/branding/hedgespark-mascot.png"
          alt="Empty state"
          width={96}
          height={96}
          className="opacity-70"
        />
        <span className="absolute -right-1 bottom-0 text-[12px] text-slate-600">💤</span>
      </div>
      <div className="text-center">
        <p className="text-[13px] text-slate-500">{message}</p>
        <p className="mt-1 text-[11px] text-slate-600">I&apos;ll notify you when something appears.</p>
      </div>
    </div>
  );
}
