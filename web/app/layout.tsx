import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Ctrl-F for Video",
  description: "Multimodal search over conference talks, on LanceDB",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
