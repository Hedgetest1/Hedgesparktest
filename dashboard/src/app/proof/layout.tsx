import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Proven Result — HedgeSpark",
  description:
    "Conversion lift measured with holdout testing. Real control group. No guessing. Proof-based revenue intelligence for Shopify.",
  robots: "noindex, nofollow",
  openGraph: {
    title: "Proven Conversion Lift — HedgeSpark",
    description:
      "Measured with holdout testing. Real control group. Proof-based revenue intelligence for Shopify.",
    type: "article",
    siteName: "HedgeSpark",
  },
  twitter: {
    card: "summary_large_image",
    title: "Proven Conversion Lift — HedgeSpark",
    description:
      "Measured with holdout testing. Real control group. Proof-based revenue intelligence for Shopify.",
  },
};

export default function ProofLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
}
