import type { Metadata } from "next";
import { Martian_Mono, Schibsted_Grotesk } from "next/font/google";
import "./globals.css";

// Schibsted Grotesk and Martian Mono stand in for LanceDB's Aeonik Pro / Aeonik
// Fono: same geometric grotesque character, but licensed for this to use.
const sans = Schibsted_Grotesk({
  subsets: ["latin"],
  variable: "--font-sans",
  weight: ["400", "500", "700", "900"],
});

const mono = Martian_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  weight: ["300", "400", "600", "700"],
});

export const metadata: Metadata = {
  title: "Ctrl-F for Video",
  description: "Multimodal search over conference talks, on LanceDB",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
