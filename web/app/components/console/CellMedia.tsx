"use client";

/** One heavy cell, shown on request and priced.
 *
 *  The console does not read heavy columns. A listing, a row browse and a query
 *  result all leave them out, and that is the claim the whole repository is an
 *  argument for — so the row browser can tell you `thumb_jpeg` holds 11 KB and has
 *  no way to draw it.
 *
 *  This is the other half of that claim rather than an exception to it. Nothing is
 *  fetched until somebody asks, the button says what asking will cost before they
 *  do, and the answer says what it actually cost afterwards. A viewer that quietly
 *  loaded every thumbnail in a page would undo the one thing this tool exists to
 *  demonstrate, so this never loads anything on mount.
 *
 *  Self-contained on purpose: it takes a table, a column and a way to name one row,
 *  and owns everything after that. Drop it into a detail pane, a cell renderer or a
 *  gallery without either of them knowing how a blob is addressed.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Icon from "@/app/components/Icon";
import { fmtBytes } from "@/app/lib/api";
import { heavyCellUrl } from "@/app/lib/catalog";

type Loaded = {
  url: string;
  mediaType: string;
  /** What the server reported reading. Not the same as the payload size: a plain
   *  binary column materialises the whole cell and the page read costs more than
   *  the bytes that came back. */
  readBytes: number;
  size: number;
};

/** What we are willing to draw, and what we will only describe.
 *
 *  An unrecognised type is named rather than shoved into an `<img>`: a broken
 *  image icon says "this is broken", and the truth is usually that the console does
 *  not know what the bytes are, which is a different sentence. */
function shape(mediaType: string): "image" | "video" | "audio" | "other" {
  if (mediaType.startsWith("image/")) return "image";
  if (mediaType.startsWith("video/")) return "video";
  if (mediaType.startsWith("audio/")) return "audio";
  return "other";
}

export function CellMedia({
  table, column, keyColumn, keyValue, bytes, className = "",
}: {
  table: string;
  column: string;
  /** The column that names a row — whatever this table uses. */
  keyColumn: string;
  keyValue: string | number;
  /** The cell's size when it is already known, so the button can say what pressing
   *  it will cost. `expand` on the rows route reports exactly this. */
  bytes?: number | null;
  className?: string;
}) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Held so it can be revoked: an object URL is a reference into the page's memory
  // and stays alive until it is released, which for a grid of thumbnails is the
  // difference between browsing a table and running out of it.
  const objectUrl = useRef<string | null>(null);

  const release = () => {
    if (objectUrl.current) {
      URL.revokeObjectURL(objectUrl.current);
      objectUrl.current = null;
    }
  };

  // A different row in the same component: drop what was shown rather than leaving
  // one row's picture under another row's name.
  useEffect(() => {
    release();
    setLoaded(null);
    setError(null);
  }, [table, column, keyColumn, keyValue]);

  useEffect(() => release, []);

  const show = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(heavyCellUrl(table, column, keyColumn, keyValue),
                              { cache: "no-store" });
      if (!res.ok) {
        let detail = `${res.status}`;
        try { detail = (await res.json()).detail ?? detail; } catch { /* not JSON */ }
        throw new Error(String(detail));
      }
      // Read before the body: `fetch` keeps the headers once the body is consumed,
      // but the cost is the point and reading it first makes that obvious.
      const mediaType = res.headers.get("content-type") ?? "application/octet-stream";
      const readBytes = Number(res.headers.get("X-Read-Bytes") ?? 0);
      const blob = await res.blob();
      release();
      objectUrl.current = URL.createObjectURL(blob);
      setLoaded({ url: objectUrl.current, mediaType, readBytes, size: blob.size });
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not read that cell");
    } finally {
      setBusy(false);
    }
  }, [table, column, keyColumn, keyValue]);

  if (error) {
    return (
      <div className={`mono text-[11px] ${className}`} style={{ color: "var(--video)" }}>
        {error}
      </div>
    );
  }

  if (!loaded) {
    const cost = typeof bytes === "number" && bytes > 0
      ? `${fmtBytes(bytes).value} ${fmtBytes(bytes).unit}`
      : null;
    return (
      <button
        type="button"
        onClick={show}
        disabled={busy}
        className={`btn mono text-[10px] tracking-[0.14em] uppercase ${className}`}
        // Said before the click rather than after. The console's argument is about
        // what a read costs, so a control that spends one should say so first.
        title={`reads ${column} for this row${cost ? ` — about ${cost}` : ""}`}
      >
        <Icon name="plus" size={14} />
        {busy ? "reading…" : cost ? `Show — ${cost}` : "Show"}
      </button>
    );
  }

  const kind = shape(loaded.mediaType);
  const read = fmtBytes(loaded.readBytes);

  return (
    <figure className={`m-0 ${className}`}>
      {kind === "image" && (
        // eslint-disable-next-line @next/next/no-img-element -- an object URL for
        // bytes this page just fetched; there is no remote to optimise.
        <img
          src={loaded.url}
          alt={`${column} of the row where ${keyColumn} is ${keyValue}`}
          className="max-w-full rounded-sm border border-[var(--rule)]"
        />
      )}
      {kind === "video" && (
        <video src={loaded.url} controls className="max-w-full rounded-sm" />
      )}
      {kind === "audio" && <audio src={loaded.url} controls className="w-full" />}
      {kind === "other" && (
        <div className="mono text-[11px] text-[var(--haze)] px-3 py-2 rounded-sm
                        border border-[var(--rule)]">
          {loaded.mediaType} — nothing here knows how to draw this
        </div>
      )}

      <figcaption className="mono text-[10px] text-[var(--dim)] mt-1.5">
        {loaded.mediaType} · {fmtBytes(loaded.size).value} {fmtBytes(loaded.size).unit}
        {" · read "}
        <span className="text-[var(--haze)]">{read.value} {read.unit}</span>
      </figcaption>
    </figure>
  );
}
