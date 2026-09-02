import type { Metadata } from "next";
import Link from "next/link";
import AppBar from "@/app/components/nav/AppBar";
import { DocsNav } from "@/app/components/docs/DocsNav";
import { SECTIONS, docIndex } from "@/app/lib/docs";

export const metadata: Metadata = {
  title: "Guide · LanceScope",
  description: "How to read a LanceDB database, and what every answer costs.",
};

export default function DocsLayout({ children }: { children: React.ReactNode }) {
  return (
    <main className="relative z-10 min-h-screen px-[var(--stage-pad)] pt-7 pb-20">
      <AppBar crumbs={[{ label: "Guide" }]}>
        <Link href="/console" className="pill">Console</Link>
      </AppBar>

      {/* `items-start` only once there is a row to start in. In the stacked
          column layout it is align-items on the cross axis, which is horizontal:
          it sized the article to its own max-content (651px inside 307px of
          phone) and widened the whole page. The rail and the article should
          stretch to the column's width until `lg` turns the axis. */}
      <div className="flex flex-col lg:flex-row gap-6 lg:gap-10 lg:items-start mt-7">
        <DocsNav docs={docIndex()} sections={SECTIONS} />
        {children}
      </div>
    </main>
  );
}
