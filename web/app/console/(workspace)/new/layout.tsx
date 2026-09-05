import type { Metadata } from "next";

// The pages themselves are client components and cannot export metadata, so each
// route carries a one-line layout whose only job is to name the tab.
export const metadata: Metadata = { title: "New database" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
