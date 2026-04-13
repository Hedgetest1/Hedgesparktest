import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Status — Hedge Spark",
  description: "Real-time operational status of Hedge Spark systems.",
  robots: "index, follow",
};

export default function StatusLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
