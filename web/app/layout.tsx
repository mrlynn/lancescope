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

// Runs before first paint, so the page never renders in one theme and then
// snaps to the other. It has to be inline and blocking for that reason — a
// component effect runs after the browser has already painted.
//
// Only an explicit choice is stamped on <html>. With nothing stored the
// attribute stays absent and the CSS media query decides, which is what makes
// "follow the OS until you say otherwise" work.
const NO_FLASH = `
try {
  var t = localStorage.getItem('lancescope-theme');
  if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t;
} catch (e) {}
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: NO_FLASH }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
