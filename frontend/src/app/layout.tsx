import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import "./globals.css";
import "../styles/base.css";
import "../styles/marketing.css";
import "../styles/auth.css";
import "../styles/dashboard.css";
import "../styles/sms.css";
import "../styles/leads.css";
import "leaflet/dist/leaflet.css";
import "leaflet.markercluster/dist/MarkerCluster.css";
import "leaflet.markercluster/dist/MarkerCluster.Default.css";
import "../styles/followups.css";
import "../styles/campaigns.css";
import "../styles/responsive.css";
import "../styles/platform-admin.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "ListingIQ — AI Seller Intelligence for Modern Brokerages",
  description:
    "Discover motivated sellers, start better conversations, and turn qualified opportunities into agent action.",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

type RootLayoutProps = Readonly<{
  children: React.ReactNode;
}>;

export default function RootLayout({ children }: RootLayoutProps) {
  return (
    <html lang="en" data-scroll-behavior="smooth">
      <body className={`${geistSans.variable} ${geistMono.variable}`}>
        {children}
      </body>
    </html>
  );
}
