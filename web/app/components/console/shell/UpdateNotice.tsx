"use client";

/** What the shell found out about versions, said in the window.
 *
 *  The desktop shell checks — once a day on launch, or whenever somebody picks
 *  *Check for Updates…* — and hands the answer over as an event. It draws nothing
 *  itself: a native alert over a console that has its own way of naming states would
 *  be a second vocabulary for the same job.
 *
 *  The states are not one state with different words. There is a newer version;
 *  there is not; the question could not be asked; it is coming down; it is going in;
 *  it is in and the window is about to be replaced; and — the one worth keeping
 *  separate — this copy cannot replace itself, which is a fact about where it was
 *  installed rather than about the update.
 *
 *  Pressing the button navigates to `/__shell/install-update`, which the shell's
 *  navigation handler swallows. It is the only thing the page says back, and it is
 *  said that way because a page on `http://127.0.0.1:<port>` is a remote origin with
 *  no IPC of its own. In a browser nothing dispatches the event, so none of this
 *  renders and the link is never followed.
 */

import { useEffect, useState } from "react";

import Icon from "@/app/components/Icon";

type State =
  | { state: "available"; version: string }
  | { state: "current"; version: string }
  | { state: "failed"; version: string }
  | { state: "downloading"; version: string }
  | { state: "installing"; version: string }
  | { state: "installed"; version: string }
  | { state: "manual"; version: string };

const RELEASES = "https://github.com/mrlynn/lancescope/releases/latest";

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

  // Once it is fetching there is no dismissing it, because dismissing it would not
  // stop it — and a download that is still running behind a toast somebody closed
  // ends in a window that restarts for no reason they can see.
  const working =
    news.state === "downloading" || news.state === "installing" || news.state === "installed";
  const tone =
    news.state === "available" || news.state === "manual"
      ? "index"
      : news.state === "failed"
        ? "video"
        : "haze";
  const pct = news.state === "downloading" ? Number(news.version) : 0;

  return (
    <div
      role="status"
      className="fixed bottom-4 right-4 z-[75] panel px-4 py-3 shadow-2xl
                 max-w-[min(380px,90vw)] flex items-start gap-2.5"
    >
      <span className="shrink-0 mt-0.5" style={{ color: `var(--${tone})` }}>
        <Icon name={news.state === "failed" ? "warning" : "info"} size={14} />
      </span>
      <div className="min-w-0 flex-1">
        {news.state === "available" && (
          <>
            <p className="text-[12px] text-[var(--bright)]">
              Version <span className="mono">{news.version}</span> is out.
            </p>
            <p className="text-[11px] text-[var(--haze)] leading-relaxed mt-1">
              It downloads, replaces this copy, and starts again where you are.
            </p>
            <div className="flex flex-wrap items-center gap-2 mt-2.5">
              <button
                // The one thing this page says to the shell. Not a fetch: a
                // navigation, which the window's handler recognises and discards.
                onClick={() => {
                  // Next's router is exactly what must not be used here: a client
                  // -side push never leaves the page, and it is the navigation
                  // itself the shell is listening for.
                  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
                  window.location.href = "/__shell/install-update";
                }}
                className="btn btn-accent inline-flex !h-[26px] mono text-[10px]
                           tracking-[0.14em] uppercase whitespace-nowrap"
              >
                <Icon name="refresh" size={12} />
                Install and restart
              </button>
              <a
                href={RELEASES}
                className="btn inline-flex !h-[26px] mono text-[10px] tracking-[0.14em]
                           uppercase whitespace-nowrap"
              >
                <Icon name="external" size={12} />
                Notes
              </a>
            </div>
          </>
        )}

        {news.state === "downloading" && (
          <>
            <p className="text-[12px] text-[var(--bright)]">Downloading the update.</p>
            {/* A bar rather than a spinner, because the wait is long enough that the
                question is "how much longer" and not "is it stuck". Zero until a
                chunk arrives carrying a length to measure against. */}
            <div
              className="mt-2 h-[3px] w-full rounded-full overflow-hidden"
              style={{ background: "rgb(var(--index-rgb) / 0.18)" }}
              role="progressbar"
              aria-valuenow={pct}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div
                className="h-full transition-[width] duration-200 ease-linear"
                style={{ width: `${pct}%`, background: "var(--index)" }}
              />
            </div>
            <p className="text-[11px] text-[var(--haze)] mt-1.5">
              <span className="mono">{pct}%</span> — you can keep working.
            </p>
          </>
        )}

        {news.state === "installing" && (
          <p className="text-[12px] text-[var(--bright)]">
            Unpacking. This copy is being replaced.
          </p>
        )}

        {news.state === "installed" && (
          <p className="text-[12px] text-[var(--bright)]">
            <span className="mono">{news.version}</span> is in. Restarting…
          </p>
        )}

        {news.state === "manual" && (
          <>
            <p className="text-[12px] text-[var(--bright)]">
              This copy cannot replace itself.
            </p>
            {/* Which is not a failure. It is where the app was installed, and the
                alternative — a macOS password prompt nobody was warned about — is
                worse than a link. */}
            <p className="text-[11px] text-[var(--haze)] leading-relaxed mt-1 break-words">
              {news.version} The release page has the disk image.
            </p>
            <a
              href={RELEASES}
              className="btn mt-2.5 inline-flex !h-[26px] mono text-[10px] tracking-[0.14em]
                         uppercase whitespace-nowrap"
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
              Could not update.
            </p>
            {/* The reason, not a shrug. This is almost always the network, and that
                is a fact about the network rather than about the software. */}
            <p className="text-[11px] text-[var(--haze)] leading-relaxed mt-1 break-words">
              {news.version}
            </p>
          </>
        )}
      </div>
      {!working && (
        <button
          onClick={() => setNews(null)}
          className="iconbtn !w-6 !h-6 shrink-0 ml-auto"
          aria-label="Dismiss"
        >
          <Icon name="close" size={12} />
        </button>
      )}
    </div>
  );
}
