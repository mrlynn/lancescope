"use client";

/** Which version of this you are looking at, at the foot of the rail.
 *
 *  Small on purpose. It is not a control and it is not news — it is the answer to
 *  a question that gets asked twice: once by somebody filing a bug, and once by
 *  somebody who has just been told an update installed and wants to see that it
 *  did. Both are better served by a number that is always there than by a dialog
 *  they have to go and find.
 *
 *  The server is asked rather than the build, because the console runs in three
 *  places and only one of them is the app. `/catalog/runtime` answers without
 *  opening a dataset, which is why the kiosk banner already uses it: this is
 *  legible with nothing configured and no database reachable.
 *
 *  Nothing is drawn until the answer arrives, and nothing is drawn if it never
 *  does. A version that says "unknown" is worse than a rail with no version in
 *  it — it invites a reader to wonder what is wrong, when the answer is that one
 *  cosmetic request failed.
 */

import { useEffect, useState } from "react";

import { getRuntime } from "@/app/lib/catalog";

export default function VersionBadge() {
  const [version, setVersion] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    getRuntime()
      .then((r) => {
        if (live && r?.app) setVersion(r.app);
      })
      .catch(() => {
        /* Cosmetic. A rail that cannot say the version is a rail without one. */
      });
    return () => {
      live = false;
    };
  }, []);

  if (!version) return null;

  return (
    <div className="mt-auto pt-4 shrink-0">
      <span
        className="inline-flex items-center rounded-full border border-[var(--rule)]
                   px-2 py-[3px] mono text-[10px] leading-none tracking-[0.08em]
                   text-[var(--haze)] select-all"
        title={`LanceScope ${version}`}
      >
        v{version}
      </span>
    </div>
  );
}
