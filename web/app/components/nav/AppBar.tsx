"use client";

/** The one header every screen wears.
 *
 *  Left: the brand, then where you are — a breadcrumb, not a path. Right: whatever
 *  this screen can do, as icons with tooltips rather than a row of words. Three
 *  pages were each drawing their own version of this and they had already drifted
 *  (one used `<Wordmark>`, one a raw `<Image>`, one showed the theme control and
 *  one did not).
 */

import Link from "next/link";
import Icon from "@/app/components/Icon";
import Wordmark from "@/app/components/Wordmark";
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
        <Link href="/" title="LanceScope — home"
              className="shrink-0 opacity-90 hover:opacity-100 transition-opacity">
          <Wordmark />
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

      <div className="flex items-center gap-2.5">
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
