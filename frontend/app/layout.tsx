import type { Metadata } from "next";
import { Geist } from "next/font/google";

import { TopBar } from "@/components/TopBar";

import "./globals.css";

const geist = Geist({ subsets: ["latin"], variable: "--font-geist" });

export const metadata: Metadata = {
  title: "Disaster Response — Dispatcher console",
  description:
    "Live delivery status for disaster alerts: every household, every channel, every reroute.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${geist.variable} h-full antialiased`}>
      <body className="min-h-full font-sans">
        <TopBar />
        {children}
      </body>
    </html>
  );
}
