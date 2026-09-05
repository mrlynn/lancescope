"use client";

/** What the shell found out about versions, said in the window.
 *
 *  The desktop shell checks — once a day on launch, or whenever somebody picks
 *  *Check for Updates…* — and hands the answer over as an event. It draws nothing
 *  itself: a native alert over a console that has its own way of naming states would
 *  be a second vocabulary for the same job.
 *
 *  Three states, and they are not the same state with different words. There is a
 *  newer version; there is not; the question could not be asked. The last one is the
 *  reason this is not a toast — "could not reach GitHub" is a fact about the network
 *  and not a fact about the software, and a reader deserves to be told which.
 *
 *  In a browser this renders nothing, ever. Nothing dispatches the event there, and
 *  a console served over the web has no copy of itself to update.
 */

import { useEffect, useState } from "react";

import Icon from "@/app/components/Icon";

type State =
  | { state: "available"; version: string }
  | { state: "current"; version: string }
  | { state: "failed"; version: string };

export default function UpdateNotice() {
  const [news, setNews] = useState<State | null>(null);

  useEffect(() => {
    const on = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail && typeof detail.state === "string") setNews(detail as State);
    };
    window.addEventListener("lancescope:update", on);
    return () => window.removeEventListener("lancescope:update", on);
  }, []);

  // "Up to date" is worth saying when it was asked for and worth forgetting after.
  // A banner that stays is a banner that gets ignored by the time it matters.
  useEffect(() => {
    if (news?.state !== "current") return;
    const t = setTimeout(() => setNews(null), 6000);
    return () => clearTimeout(t);
  }, [news]);

  if (!news) return null;

  const tone = news.state === "available" ? "index" : news.state === "failed" ? "video" : "haze";
  return (
    <div
      role="status"
      className="fixed bottom-4 right-4 z-[75] panel px-4 py-3 shadow-2xl
                 max-w-[min(380px,90vw)] flex items-start gap-2.5"
    >
      <span className="shrink-0 mt-0.5" style={{ color: `var(--${tone})` }}>
        <Icon name={news.state === "failed" ? "warning" : "info"} size={14} />
      </span>
      <div className="min-w-0">
        {news.state === "available" && (
          <>
            <p className="text-[12px] text-[var(--bright)]">
              Version <span className="mono">{news.version}</span> is out.
            </p>
            <p className="text-[11px] text-[var(--haze)] leading-relaxed mt-1">
              This copy will not replace itself. The release page has the disk image.
            </p>
            <a
              href="https://github.com/mrlynn/lancescope/releases/latest"
              className="btn mt-2.5 inline-flex !h-[26px] mono text-[10px] tracking-[0.14em]
                         uppercase"
            >
              <Icon name="external" size={12} />
              Release notes
            </a>
          </>
        )}
        {news.state === "current" && (
          <p className="text-[12px] text-[var(--haze)]">
            <span className="mono text-[var(--bright)]">{news.version}</span> is the
            newest there is.
          </p>
        )}
        {news.state === "failed" && (
          <>
            <p className="text-[12px] text-[var(--bright)]">
              Could not check for updates.
            </p>
            {/* The reason, not a shrug. This is almost always the network, and that
                is a fact about the network rather than about the software. */}
            <p className="text-[11px] text-[var(--haze)] leading-relaxed mt-1 break-words">
              {news.version}
            </p>
          </>
        )}
      </div>
      <button
        onClick={() => setNews(null)}
        className="iconbtn !w-6 !h-6 shrink-0 ml-auto"
        aria-label="Dismiss"
      >
        <Icon name="close" size={12} />
      </button>
    </div>
  );
}
