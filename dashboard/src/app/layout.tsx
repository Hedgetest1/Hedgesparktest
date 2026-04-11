import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { ErrorReporterInstaller } from "./components/ErrorReporterInstaller";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "HedgeSpark — The AI Revenue Leak Detector for Shopify",
  description:
    "The AI revenue leak detector for Shopify. Find the money you're silently losing, stop the bleed, and prove the recovery. Built for serious Shopify merchants.",
  keywords: [
    "Shopify app",
    "Shopify AI",
    "Shopify revenue leak detector",
    "revenue leak detection",
    "loss prevention Shopify",
    "Shopify revenue recovery",
    "Shopify analytics",
    "Shopify conversion optimization",
    "AI for Shopify",
    "silent revenue loss",
  ],
  openGraph: {
    title: "HedgeSpark — The AI Revenue Leak Detector for Shopify",
    description:
      "Find the money you're silently losing. Stop the bleed. Prove the recovery.",
    type: "website",
    siteName: "HedgeSpark",
  },
  twitter: {
    card: "summary_large_image",
    title: "HedgeSpark — The AI Revenue Leak Detector for Shopify",
    description:
      "Find the money you're silently losing. Stop the bleed. Prove the recovery.",
  },
};

const softwareApplicationJsonLd = {
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  name: "HedgeSpark",
  applicationCategory: "BusinessApplication",
  applicationSubCategory: "Shopify App",
  operatingSystem: "Shopify",
  description:
    "AI-powered revenue leak detection for Shopify stores. Finds silent revenue loss, stops the bleed, and proves the recovery.",
  url: "https://hedgesparkhq.com",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        {/* Tracker script — served from the backend API */}
        {process.env.NEXT_PUBLIC_API_BASE_URL && (
          <script
            src={`${process.env.NEXT_PUBLIC_API_BASE_URL}/tracker.js`}
            defer
          ></script>
        )}
        {/* Structured data for SEO discoverability */}
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify(softwareApplicationJsonLd),
          }}
        />
      </head>
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        <ErrorReporterInstaller />
        {children}
      </body>
    </html>
  );
}
