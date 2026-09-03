"use client";

/** The one header every screen wears.
 *
 *  Left: the brand, then where you are — a breadcrumb, not a path. Right: whatever
 *  this screen can do, as icons with tooltips rather than a row of words. Three
 *  pages were each drawing their own version of this and they had already drifted
 *  (one used a wordmark, one a raw `<Image>`, one showed the theme control and
 *  one did not).
 *
 *  The brand here is LanceScope's own: the mark from brand/lancescope/mark.svg and
 *  the name set in type. It used to be LanceDB's wordmark, which inside a tool for
 *  reading LanceDB read as attribution, but is the wrong thing to wear as an
 *  identity — this project is not theirs. The title says so on hover.
 */

import Link from "next/link";
import Icon from "@/app/components/Icon";
import Mark from "@/app/components/Mark";
import ThemeToggle from "@/app/components/ThemeToggle";

export type Crumb = { label: string; href?: string };

export default function AppBar({
  crumbs,
  children,
  settingsHref = "/console/settings",
  showSettings = true,
}: {
  crumbs: Crumb[];
  children?: React.ReactNode;
  settingsHref?: string;
  showSettings?: boolean;
}) {
  return (
    <header className="flex items-center justify-between gap-4 flex-wrap mb-8">
      <div className="flex items-center gap-3.5 min-w-0">
        <Link
          href="/"
          title="LanceScope — an independent tool, not affiliated with LanceDB"
          className="shrink-0 flex items-center gap-2.5 opacity-90 hover:opacity-100
                     transition-opacity"
        >
          <Mark size={22} className="text-[var(--haze)]" />
          <span className="text-[17px] font-extrabold tracking-tight text-[var(--bright)]">
            LanceScope
          </span>
        </Link>
        {crumbs.map((c, i) => (
          <div key={`${c.label}-${i}`} className="flex items-center gap-3.5 min-w-0">
            <span className="text-[var(--dim)]" aria-hidden>
              <Icon name="chevronRight" size={13} />
            </span>
            {c.href ? (
              <Link
                href={c.href}
                className="text-[17px] font-bold tracking-tight text-[var(--haze)]
                           hover:text-[var(--bright)] transition-colors truncate"
              >
                {c.label}
              </Link>
            ) : (
              <h1 className="text-[17px] font-bold tracking-tight text-[var(--bright)] truncate">
                {c.label}
              </h1>
            )}
          </div>
        ))}
      </div>

      {/* Wraps, and wraps to the right. The header itself has always wrapped, but
          this group did not, so on a phone the console's own actions — a cost
          readout, two links, the guide, the theme control and settings — sat in one
          unbreakable row 466px wide inside a 375px viewport and pushed the layout
          viewport out with them, cutting every page off at the right edge.
          `justify-end` so a wrapped second line stays under the first rather than
          drifting left into the crumbs. */}
      <div className="flex items-center justify-end flex-wrap gap-2.5 min-w-0">
        {children}
        {/* On every screen, because the question a guide answers arrives while you
            are looking at the thing you do not understand. */}
        <Link href="/docs/index" className="iconbtn" data-tip="Guide"
              data-tip-side="left" aria-label="Guide">
          <Icon name="info" size={16} />
        </Link>
        <ThemeToggle />
        {showSettings && (
          <Link href={settingsHref} className="iconbtn" data-tip="Settings" data-tip-side="left"
                aria-label="Settings">
            <Icon name="settings" size={16} />
          </Link>
        )}
      </div>
    </header>
  );
}
