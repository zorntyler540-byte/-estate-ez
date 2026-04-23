import "./globals.css";
import { Source_Sans_3 } from "next/font/google";

const sans = Source_Sans_3({
  subsets: ["latin"],
  display: "swap"
});

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={sans.className}>
      <body className="ct-body">{children}</body>
    </html>
  );
}

