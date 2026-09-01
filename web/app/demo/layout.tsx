import type { Metadata } from "next";

export const metadata: Metadata = { title: "Ctrl-F for Video" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
