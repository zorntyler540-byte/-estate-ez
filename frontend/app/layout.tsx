import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Source_Sans_3 } from "next/font/google";
import { ToastProvider } from "@/components/ui/toast";

const sans = Source_Sans_3({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-source-sans-3"
});

export const metadata: Metadata = {
  title: {
    default: "Estate-EZ",
    template: "%s · Estate-EZ"
  },
  description: "Estate-EZ — manage estate sale lots, photos, and AI-assisted cataloging."
};

export const viewport: Viewport = {
  themeColor: "#070708",
  width: "device-width",
  initialScale: 1
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.className} ${sans.variable} dark`}>
      <body className="ct-body">
        <ToastProvider>{children}</ToastProvider>
      </body>
    </html>
  );
}

