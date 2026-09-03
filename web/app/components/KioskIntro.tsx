"use client";

/** What a public demo is, said once, before anyone works it out by being refused.
 *
 *  The strip along the top can carry "read-only" and a link, and that is all it can
 *  carry. It cannot say why the query box stops answering after four queries, or why
 *  the settings page will not take a database, or that the numbers on screen are
 *  real measurements rather than a rehearsed screenshot. Someone who discovers those
 *  by hitting them concludes the tool is broken, which is the opposite of what a
 *  demo is for.
 *
 *  So: once, on the first visit, and then never again unless asked for. The banner
 *  keeps a button that reopens it, because a dialog you can dismiss for ever and
 *  never recover is a worse deal than it looks — the sentence you needed is usually
 *  the one you skimmed.
 *
 *  `<dialog>` for the same reasons Diagrams.tsx uses one: the top layer, the
 *  backdrop, Escape and returning focus where it came from are all things the
 *  element does and none of them are worth reimplementing.
 */

import Link from "next/link";
import { useEffect, useRef } from "react";
import Icon from "@/app/components/Icon";

const DOWNLOAD = "https://lancescope.mlynn.dev/download";
const REPO = "https://github.com/mrlynn/lancescope";

export default function KioskIntro({
  open,
  onClose,
  dataset,
}: {
  open: boolean;
  onClose: () => void;
  dataset: string | null;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    // showModal() on an already-open dialog throws, and close() on a closed one is
    // a no-op that still fires nothing. Both guarded rather than assumed.
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      className="kiosk-dialog"
      aria-labelledby="kiosk-intro-title"
      // Escape and the backdrop both close it, and both are dismissals worth
      // remembering — someone who pressed Escape has decided, the same as someone
      // who clicked the button.
      onClose={onClose}
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
    >
      <div className="kiosk-sheet">
        <div className="flex items-start justify-between gap-4 mb-5">
          <div>
            <div className="eyebrow mb-2">public demo</div>
            <h2
              id="kiosk-intro-title"
              className="text-[22px] md:text-[26px] font-extrabold tracking-tight
                         text-[var(--bright)]"
            >
              A real console, on somebody else&rsquo;s dataset
            </h2>
          </div>
          <button type="button" className="iconbtn shrink-0" onClick={onClose}
                  aria-label="Close">
            <Icon name="close" size={15} />
          </button>
        </div>

        <p className="text-[14px] leading-relaxed text-[var(--body)] max-w-[64ch]">
          This is the same build the download gives you, pointed at a public LanceDB
          table{dataset ? <> — <span className="mono text-[var(--bright)]">{dataset}</span></> : null}.
          Nothing on screen is staged. The schema, the versions, the findings and the
          byte counts all come from Lance&rsquo;s own IO counters, measured as you
          click. Watch the number beside the title change while you browse.
        </p>

        <h3 className="eyebrow mt-7 mb-3">What it will not do here</h3>
        <ul className="space-y-3.5">
          <Limit title="It cannot write.">
            No route in the console creates, compacts, restores or deletes anything —
            true of every LanceScope, not just this one. The only file the whole
            project writes is its own settings file, and this deployment will not
            write even that.
          </Limit>
          <Limit title="It is pinned to one table.">
            You cannot point it at your own database from here. The dataset is read
            straight from HuggingFace over <code className="mono text-[12px]">hf://</code>,
            so this demo stores no data at all — only the bytes you actually look at
            ever move.
          </Limit>
          <Limit title="The language layer is off.">
            It bills a model provider per request, so a console shared with strangers
            does not carry one. Nothing else loses anything: every finding on the
            other tabs is derived from metadata by rules, with no model involved.
          </Limit>
          <Limit title="Queries are rate limited.">
            Each one is paid for in HTTP range requests against a server that meters
            us, so a few in a row will be refused for a moment. Schema, versions,
            indices, fragments, findings and row browsing are never limited — they
            are the cheap half, and the half worth seeing.
          </Limit>
        </ul>

        <p className="text-[13px] leading-relaxed text-[var(--haze)] mt-6 max-w-[64ch]">
          That last limit is close to the point of the project. A row browse of a
          table holding gigabytes costs kilobytes, because the bytes a query touches
          and the bytes a table holds live in different files. Run it against a
          database of your own and there is no meter at all — it reads from disk.
        </p>

        <div className="flex flex-wrap items-center gap-2.5 mt-7 pt-5
                        border-t border-[var(--hairline)]">
          <button type="button" className="btn btn-accent" onClick={onClose}>
            Look around
          </button>
          {/* The guide ships inside this image, so it is a navigation rather than
              a trip to the marketing site: whatever is running here is documented
              by exactly these pages. */}
          <Link className="btn" href="/docs/index" onClick={onClose}>
            <Icon name="info" size={14} />
            Read the guide
          </Link>
          <a className="btn" href={DOWNLOAD} target="_blank" rel="noreferrer">
            <Icon name="external" size={14} />
            Run it on your own data
          </a>
          <a className="btn" href={REPO} target="_blank" rel="noreferrer">
            Source
          </a>
        </div>
      </div>
    </dialog>
  );
}

/** One constraint, with the reason attached.
 *
 *  A rule down the left rather than an icon: four identical glyphs in a column say
 *  nothing that the indent does not, and the amber is doing the work of marking
 *  these as costs rather than features. */
function Limit({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <li className="pl-3.5 border-l-2" style={{ borderColor: "var(--index)" }}>
      <p className="text-[13.5px] leading-relaxed text-[var(--body)] max-w-[62ch]">
        <span className="text-[var(--bright)] font-semibold">{title}</span>{" "}
        {children}
      </p>
    </li>
  );
}
