"use client";

/** The headings of the page being read, with the one in view marked.
 *
 *  Only rendered when a page has enough of them to be worth navigating — a table of
 *  contents listing two items is furniture. */

import { useEffect, useState } from "react";
import type { Heading } from "@/app/lib/docs";

export function OnThisPage({ headings }: { headings: Heading[] }) {
  const [active, setActive] = useState<string | null>(null);

  useEffect(() => {
    if (!headings.length) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setActive(visible[0].target.id);
      },
      // Weighted to the top of the viewport: the heading you are reading under is
      // the one just above the middle, not whatever is centred.
      { rootMargin: "-80px 0px -60% 0px", threshold: 0 },
    );
    for (const h of headings) {
      const el = document.getElementById(h.id);
      if (el) observer.observe(el);
    }
    return () => observer.disconnect();
  }, [headings]);

  if (headings.length < 3) return null;

  return (
    <aside className="hidden xl:block w-[200px] shrink-0">
      <div className="sticky top-8">
        <div className="eyebrow mb-2.5">On this page</div>
        <ul className="space-y-1.5">
          {headings.map((h) => (
            <li key={h.id} style={{ paddingLeft: (h.level - 2) * 10 }}>
              <a
                href={`#${h.id}`}
                className="block text-[12px] leading-snug transition-colors"
                style={{ color: active === h.id ? "var(--video)" : "var(--haze)" }}
              >
                {h.text}
              </a>
            </li>
          ))}
        </ul>
      </div>
    </aside>
  );
}
