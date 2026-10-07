import type { Metadata } from "next";
import { Footer } from "@/components/Footer";
import "./globals.css";
import "../lib/bigintPolyfill";

export const metadata: Metadata = {
  title: "GitEscrow — code-verified milestone escrow",
  description: "Autonomous milestone escrow on GenLayer: validators inspect GitHub commits, CI and coverage, then release funds.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen font-sans antialiased">{children}
        <Footer />
      </body>
    </html>
  );
}
