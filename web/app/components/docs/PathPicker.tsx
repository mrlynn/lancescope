"use client";

/** Choose a way in.
 *
 *  Three doors rather than five. The five roles this was asked for overlap heavily —
 *  an ML engineer and a data scientist want the same seven pages in nearly the same
 *  order — and a picker whose options are hard to tell apart is a picker that makes
 *  somebody guess. Each door is named for what you are trying to do, with the job
 *  titles underneath for anyone who would rather match on those.
 *
 *  Choosing is not a commitment: it reorders the guide and can be undone from the
 *  same control, and every page remains reachable from the sidebar either way.
 */

import Link from "next/link";
import Icon, { type IconName } from "@/app/components/Icon";
import { PATHS } from "@/app/lib/paths";
import { choosePath, useChosenPath } from "@/app/components/docs/path-state";

const GLYPH: Record<string, IconName> = {
  "build-models": "spark",
  "run-production": "system",
  "build-on-it": "schema",
};

export function PathPicker({ titles }: { titles: Record<string, string> }) {
  const chosen = useChosenPath();

  if (chosen) {
    const first = chosen.steps[0];
    return (
      <section className="panel p-5 mt-8">
        <div className="flex items-start gap-3 flex-wrap">
          <span className="pt-0.5 shrink-0" style={{ color: "var(--video)" }}>
            <Icon name={GLYPH[chosen.id] ?? "spark"} size={17} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="eyebrow mb-1">your path</div>
            <div className="text-[15px] text-[var(--bright)]">{chosen.label}</div>
            <div className="mono text-[11px] text-[var(--haze)] mt-1">{chosen.roles}</div>
            <p className="text-[13px] text-[var(--body)] leading-relaxed mt-2.5 max-w-[62ch]">
              {chosen.steps.length} pages, in the order they make sense in. The
              sidebar still holds the whole guide.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2 shrink-0">
            <Link href={`/docs/${first.slug}`} className="btn btn-accent">
              <Icon name="play" size={14} />
              {`Start`}
            </Link>
            <button className="btn" onClick={() => choosePath(null)}>
              <Icon name="close" size={14} />
              Clear
            </button>
          </div>
        </div>

        <ol className="mt-5 space-y-1.5">
          {chosen.steps.map((s, i) => (
            <li key={s.slug}>
              <Link
                href={`/docs/${s.slug}`}
                className="flex items-baseline gap-3 px-3 py-2 rounded-sm
                           hover:bg-[rgb(var(--index-rgb)/0.07)] transition-colors"
              >
                <span className="mono text-[10px] text-[var(--dim)] w-4 shrink-0">
                  {i + 1}
                </span>
                <span className="min-w-0">
                  <span className="block text-[13px] text-[var(--bright)]">
                    {titles[s.slug] ?? s.slug}
                  </span>
                  <span className="block text-[12px] text-[var(--haze)] leading-relaxed mt-0.5">
                    {s.why}
                  </span>
                </span>
              </Link>
            </li>
          ))}
        </ol>
      </section>
    );
  }

  return (
    <section className="mt-8">
      <div className="eyebrow mb-2">Choose your path</div>
      <p className="text-[13px] text-[var(--body)] leading-relaxed mb-4 max-w-[62ch]">
        The guide is arranged by kind — something to follow, something to do,
        something to look up, something to understand. That is a good way to keep
        documentation and a poor way to start reading it. Pick what you are here to
        do and it will be put in an order instead.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {PATHS.map((p) => (
          <button
            key={p.id}
            onClick={() => choosePath(p.id)}
            className="panel p-4 text-left transition-colors
                       hover:border-[var(--video)]
                       hover:bg-[rgb(var(--video-rgb)/0.06)]"
          >
            <span className="flex items-center gap-2.5 mb-2"
                  style={{ color: "var(--video)" }}>
              <Icon name={GLYPH[p.id] ?? "spark"} size={16} />
              <span className="text-[14px] text-[var(--bright)]">{p.label}</span>
            </span>
            <span className="mono block text-[10px] text-[var(--haze)] mb-2">
              {p.roles}
            </span>
            <span className="block text-[12px] text-[var(--body)] leading-relaxed">
              {p.blurb}
            </span>
            <span className="mono block text-[10px] text-[var(--dim)] mt-3">
              {p.steps.length} pages
            </span>
          </button>
        ))}
      </div>

      <p className="text-[12px] text-[var(--haze)] leading-relaxed mt-4 max-w-[62ch]">
        Remembered on this machine, changed whenever you like, and it hides nothing —
        every page stays in the sidebar.
      </p>
    </section>
  );
}
