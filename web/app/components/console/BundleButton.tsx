"use client";

/** Taking the whole diagnosis with you.
 *
 *  Two buttons rather than one, because the two files answer different questions:
 *  markdown is what gets pasted into an issue and read by a person, JSON is what
 *  another console opens and a script parses. Offering only one would mean guessing
 *  which conversation this is about to become.
 *
 *  Both are assembled by the server from the routes this console already called, so
 *  what leaves here cannot disagree with what is on screen. The line underneath says
 *  what does not leave — the rows, and the path — because a document you are about
 *  to hand somebody is exactly when that is worth knowing, and afterwards is too
 *  late.
 */

import { useState } from "react";

import Icon from "@/app/components/Icon";
import {
  ApiError, getBundleMarkdown, getBundleWithQuery, getBundle,
  type QuerySpec, type StoredQuerySpec,
} from "@/app/lib/catalog";
import { downloadBundleJson, downloadBundleMarkdown } from "@/app/lib/export";

const BTN = "btn mono !h-[26px] !px-2.5 text-[10px] tracking-[0.14em] uppercase";

export default function BundleButton({ table, spec, saved = [], facet, note }: {
  table: string;
  /** When present, the query and its diagnosis go into the document — and it runs
   *  on the server rather than being handed in already run, so the result and the
   *  findings describe the same moment of the same table. */
  spec?: QuerySpec;
  saved?: StoredQuerySpec[];
  facet?: string;
  /** What this particular placement is offering, in the caller's words. */
  note?: string;
}) {
  const [busy, setBusy] = useState<"md" | "json" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function take(kind: "md" | "json") {
    setBusy(kind);
    setError(null);
    try {
      if (kind === "md") {
        downloadBundleMarkdown(table,
          await getBundleMarkdown(table, { facet }, spec, saved));
      } else {
        downloadBundleJson(table, spec
          ? await getBundleWithQuery(table, spec, saved, { facet })
          : await getBundle(table, { facet }));
      }
    } catch (e) {
      // Named, not swallowed: a bundle that silently did not download looks
      // identical to a browser that blocked the file.
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mt-5 pt-4 border-t border-[var(--rule)]">
      <div className="flex flex-wrap items-center gap-2">
        <span className="eyebrow">hand this to someone</span>
        <button className={BTN} disabled={busy !== null} onClick={() => take("md")}>
          <Icon name="external" size={12} />{busy === "md" ? "…" : "markdown"}
        </button>
        <button className={BTN} disabled={busy !== null} onClick={() => take("json")}>
          <Icon name="external" size={12} />{busy === "json" ? "…" : "json"}
        </button>
        <span className="mono text-[10px] text-[var(--haze)]">
          {note ?? "everything on this table"} — no rows, no credentials, and the
          database root redacted
        </span>
      </div>
      {error && (
        <p className="mono text-[10px] text-[var(--video)] mt-2">
          the bundle could not be built — {error}
        </p>
      )}
    </div>
  );
}
