"use client";

/** The sidebar, and the search that filters it.
 *
 *  Search is a filter over titles, summaries and headings rather than full text.
 *  The whole guide is seventeen pages: a reader who types "blob" wants the page
 *  about blobs, not the eleven paragraphs that mention one.
 *
 *  Below `lg` there is no column to be a sidebar in, and the rail was stacking
 *  above the article: seventeen links and a search box between the top of the
 *  screen and the first line of the page you had just chosen. On a phone it
 *  collapses to one row naming where you are, and opens when you ask it to. */

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
  const [open, setOpen] = useState(false);
  const pathname = usePathname();
  const here = docs.find((d) => pathname === `/docs/${d.slug}`);

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
      {/* The closed state, and the only part of the rail a phone sees until it is
          asked for more. It names the page you are on, because "Contents" alone
          tells a reader who has scrolled nothing they did not know. */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="lg:hidden w-full flex items-center gap-2.5 px-3.5 h-[38px] mb-4
                   rounded-sm text-[13px] text-left"
        style={{ background: "var(--ink-3)", border: "1px solid var(--rule)",
                 color: "var(--body)" }}
      >
        <Icon name="rows" size={14} />
        <span className="truncate flex-1">{here?.title ?? "All pages"}</span>
        <span className="eyebrow shrink-0">{open ? "close" : "contents"}</span>
        {/* inline-flex, so the box is the glyph and the half-turn spins about the
            chevron's own centre rather than a taller line box's. */}
        <span className="shrink-0 inline-flex transition-transform"
              style={{ transform: open ? "rotate(180deg)" : undefined }}>
          <Icon name="chevronDown" size={14} />
        </span>
      </button>

      <div className={open ? "block" : "hidden lg:block"}>
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
                        // Choosing a page is the end of using the menu. Closed on
                        // the click rather than in an effect watching the route,
                        // which would be a render triggering a render.
                        onClick={() => setOpen(false)}
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
      </div>
    </nav>
  );
}
