"use client";

/** Taking a result out of the console.
 *
 *  What leaves is what was on screen: the projected columns of the rows already
 *  read. Heavy and blob columns are not in a query result to begin with — the
 *  server leaves them out of every projection — so there is nothing here that could
 *  export one, and the summaries that stand in for them are exported as the
 *  summaries they are rather than as data that was never read.
 */

import type { Cell } from "@/app/lib/catalog";

/** A cell as text. The summaries the server sends in place of heavy values stay
 *  summaries: writing `blob 16.7 MB` into a CSV is honest, and writing an empty
 *  column would suggest the value was null rather than never read. */
export function cellText(v: Cell): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "object") {
    if ("blob" in v) {
      return v.size_bytes === null ? "blob (size unknown, not read)"
                                   : `blob ${v.size_bytes} bytes, not read`;
    }
    if ("vector_dim" in v) return `vector[${v.vector_dim}], not read`;
    if ("bytes" in v) return `${v.bytes} bytes`;
    return JSON.stringify(v);
  }
  return String(v);
}

function csvField(s: string): string {
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function toCsv(columns: string[], rows: Record<string, Cell>[]): string {
  const head = columns.map((c) => csvField(c)).join(",");
  const body = rows.map((r) => columns.map((c) => csvField(cellText(r[c]))).join(","));
  return [head, ...body].join("\n");
}

export function toJson(columns: string[], rows: Record<string, Cell>[]): string {
  return JSON.stringify(
    rows.map((r) => Object.fromEntries(columns.map((c) => [c, r[c] ?? null]))),
    null,
    2,
  );
}

/** Hand the file to the browser. Same-origin blob, revoked once the click is done —
 *  a URL left alive holds the whole result in memory for as long as the tab is. */
export function download(filename: string, contents: string, type: string) {
  const url = URL.createObjectURL(new Blob([contents], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
