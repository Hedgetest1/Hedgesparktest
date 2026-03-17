import Image from "next/image";

export function MascotLoader({
  caption = "Reading the signals…",
}: {
  caption?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 py-20">
      <div className="relative">
        <Image
          src="/branding/hedgespark-mascot.png"
          alt="Loading"
          width={88}
          height={88}
          className="hs-bob"
          priority
        />
        <span className="hs-sparkle absolute -right-1 -top-1 text-xl leading-none text-amber-300">
          ✦
        </span>
      </div>
      <p className="text-sm text-slate-400">{caption}</p>
    </div>
  );
}

export function MascotEmpty({
  message = "No sparks yet — check back soon.",
}: {
  message?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-white/[0.08] bg-white/[0.02] py-12">
      <Image
        src="/branding/hedgespark-mascot.png"
        alt="Empty state"
        width={52}
        height={52}
        className="opacity-50"
      />
      <p className="text-sm text-slate-500">{message}</p>
    </div>
  );
}
