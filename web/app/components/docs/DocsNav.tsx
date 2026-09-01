"use client";

/** The sidebar, and the search that filters it.
 *
 *  Search is a filter over titles, summaries and headings rather than full text.
 *  The whole guide is seventeen pages: a reader who types "blob" wants the page
 *  about blobs, not the eleven paragraphs that mention one. */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useMemo, useState } from "react";
import Icon from "@/app/components/Icon";
import type { DocIndexEntry } from "@/app/lib/docs";

export function DocsNav({ docs, sections }: {
  docs: DocIndexEntry[];
  sections: readonly string[];
}) {
  const [q, setQ] = useState("");
  const pathname = usePathname();

  const matches = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return docs;
    return docs.filter((d) =>
      [d.title, d.summary, d.section, ...d.headings]
        .join(" ")
        .toLowerCase()
        .includes(needle),
    );
  }, [docs, q]);

  return (
    <nav className="w-full lg:w-[248px] shrink-0">
      <div className="relative mb-5">
        <span className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--dim)]">
          <Icon name="search" size={14} />
        </span>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search the guide"
          className="inp !pl-9"
          aria-label="Search the guide"
        />
      </div>

      {matches.length === 0 && (
        <p className="text-[12px] text-[var(--haze)] leading-relaxed">
          Nothing matches <span className="mono text-[var(--bright)]">{q}</span>.
        </p>
      )}

      <div className="space-y-6">
        {sections.map((section) => {
          const inSection = matches.filter((d) => d.section === section);
          if (!inSection.length) return null;
          return (
            <div key={section}>
              <div className="eyebrow mb-2">{section}</div>
              <ul className="space-y-0.5">
                {inSection.map((d) => {
                  const on = pathname === `/docs/${d.slug}`;
                  return (
                    <li key={d.slug}>
                      <Link
                        href={`/docs/${d.slug}`}
                        title={d.summary}
                        className="block px-2.5 py-1.5 rounded-sm text-[13px] leading-snug
                                   transition-colors"
                        style={on
                          ? { color: "var(--video)",
                              background: "rgb(var(--video-rgb) / 0.09)" }
                          : { color: "var(--body)" }}
                      >
                        {d.title}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}
      </div>
    </nav>
  );
}
