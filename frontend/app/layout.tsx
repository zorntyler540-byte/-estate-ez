import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Source_Sans_3 } from "next/font/google";

const sans = Source_Sans_3({
  subsets: ["latin"],
  display: "swap"
});

export const metadata: Metadata = {
  title: {
    default: "Caring Transitions — Estate inventory",
    template: "%s · Caring Transitions"
  },
  description: "Sign in to manage estate sale lots, photos, and AI-assisted cataloging."
};

export const viewport: Viewport = {
  themeColor: "#070708",
  width: "device-width",
  initialScale: 1
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={sans.className}>
      <body className="ct-body">{children}</body>
    </html>
  );
}

