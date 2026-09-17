import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Dispatcher console",
  description: "Live delivery status for disaster alerts.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full">{children}</body>
    </html>
  );
}
