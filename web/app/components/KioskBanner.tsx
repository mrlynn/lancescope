"use client";

/** The strip that says this console is not yours, and the door back to why.
 *
 *  A public demo looks exactly like a local one — same screens, same numbers — and
 *  that is the point of it. It also means a visitor has no way to know why saving a
 *  connection refuses, why the settings page has no write controls, or why a fifth
 *  query in a row is turned away, unless something says so before they try. The
 *  strip is the permanent half of that; KioskIntro is the half that can actually
 *  explain, shown once and then only on request.
 *
 *  Renders nothing at all off a kiosk, which is every local run and the desktop
 *  app. The one request it makes is `/catalog/runtime`, which the console already
 *  fetches on the settings page and which answers without opening a dataset.
 */

import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import Icon from "@/app/components/Icon";
import KioskIntro from "@/app/components/KioskIntro";
import { getRuntime } from "@/app/lib/catalog";
import { dbName } from "@/app/lib/dbname";
import { getSettings } from "@/app/lib/settings";

const DOWNLOAD = "https://lancescope.mlynn.dev/download";

/** Remembered per browser, not per session: the explanation is worth reading once,
 *  and a modal on every visit is an advert. */
const SEEN = "lancescope-kiosk-intro";

function alreadySeen(): boolean {
  try {
    return localStorage.getItem(SEEN) === "1";
  } catch {
    // Private windows and blocked site data. Erring towards showing it is right —
    // the cost of a second read is a dismissed dialog, the cost of never showing it
    // is someone deciding the tool is broken.
    return false;
  }
}

function markSeen() {
  try {
    localStorage.setItem(SEEN, "1");
  } catch {
    /* Not remembered. It will open again next time, which is survivable. */
  }
}

export default function KioskBanner() {
  const [kiosk, setKiosk] = useState(false);
  const [name, setName] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    let live = true;
    getRuntime()
      .then((r) => {
        if (!live || !r.kiosk) return;
        setKiosk(true);

        // Not over the guide. Someone reading documentation has already been told
        // what this is, and a modal across a page of prose is an interruption
        // rather than an introduction. The banner still offers the button.
        if (!alreadySeen() && !pathname?.startsWith("/docs")) {
          setOpen(true);
        }

        // Only once we know it is a kiosk, because naming the database is the
        // second sentence and not worth a request on every other deployment.
        return getSettings().then((s) => {
          if (live) setName(dbName(s.root?.root));
        });
      })
      .catch(() => {
        /* An unreachable API is the console's own problem to report, not this
           strip's — saying "public demo" when nothing answered would be a guess. */
      });
    return () => {
      live = false;
    };
    // Deliberately once per mount. `pathname` is read for the first decision only;
    // re-running this on every navigation would reopen the dialog on the way out of
    // the guide, which is exactly the behaviour the check exists to prevent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Dismissal is dismissal however it arrived — the button, Escape, or the
  // backdrop. All three land here, and all three are remembered.
  const dismiss = useCallback(() => {
    setOpen(false);
    markSeen();
  }, []);

  if (!kiosk) return null;

  return (
    <>
      <div
        className="flex items-center justify-center gap-2.5 flex-wrap px-4 py-2
                   text-[12px] border-b border-[var(--hairline)]
                   bg-[var(--ink-3)] text-[var(--haze)]"
        role="status"
      >
        <span style={{ color: "var(--index)" }} aria-hidden>
          <Icon name="info" size={13} />
        </span>
        <span>
          Public demo — read-only
          {name ? (
            <>
              , pinned to <span className="mono text-[var(--bright)]">{name}</span>
            </>
          ) : null}
          .
        </span>
        {/* The way back in. A dialog that can be dismissed for ever and never
            recovered is a worse deal than it looks: the sentence you needed is
            usually the one you skimmed. */}
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="underline underline-offset-2 hover:text-[var(--bright)]
                     transition-colors"
        >
          What does that mean?
        </button>
        <span aria-hidden className="text-[var(--dim)]">·</span>
        <a
          href={DOWNLOAD}
          className="underline underline-offset-2 hover:text-[var(--bright)] transition-colors"
          style={{ color: "var(--video)" }}
        >
          Run it on your own database →
        </a>
      </div>

      <KioskIntro open={open} onClose={dismiss} dataset={name} />
    </>
  );
}
