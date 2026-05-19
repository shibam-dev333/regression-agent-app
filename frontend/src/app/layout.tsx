import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SBPPA Regression Agent",
  description: "OnBase 26.1 regression assistant — Phase 0 scaffold",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
