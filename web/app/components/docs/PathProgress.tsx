"use client";

/** Where you are on your path, and what comes next.
 *
 *  Two components, because a reader on a path needs two different things in two
 *  different places: a marker at the top of a page saying which step this is, and a
 *  replacement for the prev/next footer that follows the path rather than the
 *  section order. The footer is the important half — following "next" through a
 *  guide sorted by Diátaxis is how somebody ends up reading the HTTP API reference
 *  because they finished Blob V2.
 *
 *  Both render nothing when there is no path, and nothing when the current page is
 *  not on it. Wandering off a path to look something up is normal, and a banner
 *  saying YOU ARE OFF YOUR PATH would be a scolding.
 */

import Link from "next/link";
import Icon from "@/app/components/Icon";
import { positionIn } from "@/app/lib/paths";
import { useChosenPath } from "@/app/components/docs/path-state";

/** "Step 3 of 7", above the page title. */
export function PathMarker({ slug }: { slug: string }) {
  const path = useChosenPath();
  if (!path) return null;
  const at = positionIn(path, slug);
  if (!at) return null;

  return (
    <span className="mono flex items-center gap-1.5 text-[10px]"
          style={{ color: "var(--video)" }}>
      <Icon name="play" size={11} />
      {path.label} · {at} of {path.steps.length}
    </span>
  );
}

type Neighbour = { slug: string; title: string } | null;

/** The one footer.
 *
 *  It has to be a single component rather than a path footer beside the old one,
 *  because "show whichever applies" is a decision, and two components each guessing
 *  at it is how a page ends up with two Next links pointing different ways. When
 *  there is no path, or this page is not on it, this is exactly the footer the guide
 *  always had.
 */
export function DocFooter({ slug, titles, prev: shelfPrev, next: shelfNext }: {
  slug: string;
  titles: Record<string, string>;
  prev: Neighbour;
  next: Neighbour;
}) {
  const path = useChosenPath();
  const at = path ? positionIn(path, slug) : 0;

  if (!path || !at) return <ShelfNav prev={shelfPrev} next={shelfNext} />;

  const prev = path.steps[at - 2];
  const next = path.steps[at];
  const done = at === path.steps.length;

  return (
    <nav className="mt-14 pt-6 max-w-[72ch]" style={{ borderTop: "1px solid var(--hairline)" }}>
      <div className="flex items-center justify-between gap-3 mb-4">
        <span className="eyebrow">{path.label}</span>
        <span className="mono text-[10px] text-[var(--dim)]">
          {at} / {path.steps.length}
        </span>
      </div>

      {/* A bar rather than a number alone: the number says where you are and the
          bar says how much is left, which is the question being asked. */}
      <div className="h-[3px] rounded-full mb-5 overflow-hidden"
           style={{ background: "var(--ink-3)" }}>
        <div className="h-full rounded-full transition-[width] duration-300"
             style={{ width: `${(at / path.steps.length) * 100}%`,
                      background: "var(--video)" }} />
      </div>

      <div className="flex flex-wrap gap-3 justify-between">
        {prev ? (
          <Link href={`/docs/${prev.slug}`} className="group max-w-[46%]">
            <div className="eyebrow mb-1">Previous</div>
            <div className="text-[13px] text-[var(--body)] group-hover:text-[var(--bright)]">
              {titles[prev.slug] ?? prev.slug}
            </div>
          </Link>
        ) : <span />}

        {next ? (
          <Link href={`/docs/${next.slug}`} className="group max-w-[46%] text-right">
            <div className="eyebrow mb-1">Next on this path</div>
            <div className="text-[13px] text-[var(--body)] group-hover:text-[var(--bright)]">
              {titles[next.slug] ?? next.slug}
            </div>
            <div className="text-[12px] text-[var(--haze)] leading-relaxed mt-1">
              {next.why}
            </div>
          </Link>
        ) : done ? (
          // The end of a path is a fact worth stating. Somebody who has read seven
          // pages should be told they are done rather than left looking for an
          // eighth.
          <div className="max-w-[60%] text-right">
            <div className="eyebrow mb-1">End of the path</div>
            <div className="text-[13px] text-[var(--body)]">
              That is {path.label} covered.{" "}
              <Link href="/docs/index" className="underline"
                    style={{ color: "var(--video)" }}>
                Pick another
              </Link>
              , or open the console.
            </div>
          </div>
        ) : <span />}
      </div>
    </nav>
  );
}

/** The guide's own order, for a reader who has not chosen a path — or who has
 *  wandered off it, which is allowed and not worth remarking on. */
function ShelfNav({ prev, next }: { prev: Neighbour; next: Neighbour }) {
  return (
    <nav className="flex flex-wrap gap-3 justify-between mt-14 pt-6 max-w-[72ch]"
         style={{ borderTop: "1px solid var(--hairline)" }}>
      {prev ? (
        <Link href={`/docs/${prev.slug}`} className="group max-w-[46%]">
          <div className="eyebrow mb-1">Previous</div>
          <div className="text-[13px] text-[var(--body)] group-hover:text-[var(--bright)]">
            {prev.title}
          </div>
        </Link>
      ) : <span />}
      {next && (
        <Link href={`/docs/${next.slug}`} className="group max-w-[46%] text-right">
          <div className="eyebrow mb-1">Next</div>
          <div className="text-[13px] text-[var(--body)] group-hover:text-[var(--bright)]">
            {next.title}
          </div>
        </Link>
      )}
    </nav>
  );
}
