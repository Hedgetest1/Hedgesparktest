import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Hedge Spark — Shopify AI Copilot",
  description:
    "Real-time visitor intent, product analytics, and conversion intelligence for Shopify merchants.",
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
      </head>
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
