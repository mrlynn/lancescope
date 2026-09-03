"use client";

/** The strip that says this console is not yours.
 *
 *  A public demo looks exactly like a local one — same screens, same numbers — and
 *  that is the point of it. It also means a visitor has no way to know why saving a
 *  connection refuses, or why the settings page has no write controls, unless the
 *  page says so before they try. So this sits above everything, on every screen,
 *  and names both the constraint and the way out of it.
 *
 *  Renders nothing at all off a kiosk, which is every local run and the desktop
 *  app. The one request it makes is `/catalog/runtime`, which the console already
 *  fetches on the settings page and which answers without opening a dataset.
 */

import { useEffect, useState } from "react";
import Icon from "@/app/components/Icon";
import { getRuntime } from "@/app/lib/catalog";
import { dbName } from "@/app/lib/dbname";
import { getSettings } from "@/app/lib/settings";

const DOWNLOAD = "https://lancescope.mlynn.dev/download";

export default function KioskBanner() {
  const [kiosk, setKiosk] = useState(false);
  const [name, setName] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    getRuntime()
      .then((r) => {
        if (!live || !r.kiosk) return;
        setKiosk(true);
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
  }, []);

  if (!kiosk) return null;

  return (
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
      <a
        href={DOWNLOAD}
        className="underline underline-offset-2 hover:text-[var(--bright)] transition-colors"
        style={{ color: "var(--video)" }}
      >
        Run it on your own database →
      </a>
    </div>
  );
}
