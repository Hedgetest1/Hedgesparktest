import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Agency — Hedge Spark",
  description: "White-label agency mode for Hedge Spark — run a roster of client stores from one console.",
  robots: "noindex, nofollow",
};

export default function AgencyLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
