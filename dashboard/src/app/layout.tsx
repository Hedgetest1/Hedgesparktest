import type { Metadata, Viewport } from "next";

export const viewport: Viewport = {
  themeColor: "#d4893a",
  width: "device-width",
  initialScale: 1,
};
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
  title: "HedgeSpark — AI Revenue Intelligence for Shopify",
  description:
    "Your Shopify store is leaking money and you don't know why. HedgeSpark finds the products that get attention but don't sell, and proves every fix against a real control group. Trust the magic.",
  keywords: [
    "Shopify app",
    "Shopify AI",
    "AI revenue intelligence",
    "Shopify revenue intelligence",
    "Shopify commerce intelligence",
    "Shopify analytics",
    "Shopify conversion optimization",
    "AI for Shopify",
    "silent revenue loss",
    "holdout-proven lift",
  ],
  openGraph: {
    title: "HedgeSpark — AI Revenue Intelligence for Shopify",
    description:
      "Your store is leaking money. You don't know why. We show you where. The most advanced dashboard built for Shopify. Stops the curse. Trust the magic.",
    type: "website",
    siteName: "HedgeSpark",
  },
  twitter: {
    card: "summary_large_image",
    title: "HedgeSpark — AI Revenue Intelligence for Shopify",
    description:
      "Your store is leaking money. You don't know why. We show you where. The most advanced dashboard built for Shopify. Stops the curse. Trust the magic.",
  },
  manifest: "/manifest.json",
  appleWebApp: {
    capable: true,
    title: "Hedge Spark",
    statusBarStyle: "black-translucent",
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
    "HedgeSpark is the AI Revenue Intelligence platform for Shopify merchants. It finds the products that get attention but don't sell, surfaces the silent revenue leaks on your store, and measures every recovery against a real control group. Trust the magic.",
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
        {/* PWA — service worker registration */}
        <script
          dangerouslySetInnerHTML={{
            __html: `
              if ('serviceWorker' in navigator && window.location.protocol === 'https:') {
                window.addEventListener('load', function () {
                  navigator.serviceWorker.register('/sw.js').catch(function(){});
                });
              }
            `.trim(),
          }}
        />
        <meta name="apple-mobile-web-app-capable" content="yes" />
        <meta name="apple-mobile-web-app-title" content="Hedge Spark" />
        <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
        <link rel="apple-touch-icon" href="/logo-beta-v2.png" />
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
