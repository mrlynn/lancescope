"use client";

/** The grid every table in this console is read through.
 *
 *  What was here before was an HTML table with `truncate` on every cell, which is
 *  fine for eight short columns and useless for the tables people actually load: a
 *  caption column ends at "The video shows a tiger in a …" and there is no gesture
 *  anywhere on the screen that finishes the sentence. A row was something you
 *  looked at, never something you could open.
 *
 *  So: a row is selectable and opens into a panel that holds every column at full
 *  length, including the ones hidden from the grid; columns can be resized, hidden
 *  and wrapped; and the header stays put while the body scrolls. Two things it
 *  deliberately does not do — it never fetches, and it never claims a sort or a
 *  count is about more than the page it was handed. Everything here operates on
 *  rows already read, which is the only honest thing a client-side grid can say. */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Icon from "@/app/components/Icon";
import { CellView } from "@/app/components/console/cell";
import { cellText } from "@/app/lib/export";
import { fmtBytes } from "@/app/lib/api";
import { CellMedia } from "@/app/components/console/CellMedia";
import type { Cell } from "@/app/lib/catalog";

export type OmittedColumn = {
  name: string;
  type: string;
  vector_dim: number | null;
  reason: string;
};

type Row = Record<string, Cell>;

const DEFAULT_WIDTH = 210;
const MIN_WIDTH = 72;

type Layout = { widths: Record<string, number>; hidden: string[]; wrap: boolean };

const NO_LAYOUT: Layout = { widths: {}, hidden: [], wrap: false };

/** The layout you gave this table last time. Widths are worth remembering — you
 *  size a caption column once per table, not once per page — and a store that is
 *  missing, full, or full of nonsense simply means the defaults. */
function remembered(key: string | undefined): Layout {
  if (!key || typeof window === "undefined") return NO_LAYOUT;
  try {
    const raw = window.localStorage.getItem(`lancescope.grid.${key}`);
    if (!raw) return NO_LAYOUT;
    const saved = JSON.parse(raw);
    return {
      widths: saved?.widths && typeof saved.widths === "object" ? saved.widths : {},
      hidden: Array.isArray(saved?.hidden) ? saved.hidden : [],
      wrap: !!saved?.wrap,
    };
  } catch {
    return NO_LAYOUT;
  }
}

/** Sorts the rows on screen, and says so. A page of 25 sorted by score is not
 *  "the top 25 by score", and the label under the grid never lets that be read
 *  the other way. */
type Sort = { col: string; dir: "asc" | "desc" } | null;

function sortKey(v: Cell): [number, number | string] {
  // Nulls and never-read summaries sort last in both directions: they are absence,
  // not a small value.
  if (v === null || v === undefined) return [2, 0];
  if (typeof v === "object") return [1, cellText(v)];
  if (typeof v === "boolean") return [0, v ? 1 : 0];
  if (typeof v === "number") return [0, v];
  return [0, String(v).toLowerCase()];
}

function compare(a: Cell, b: Cell): number {
  const [ra, ka] = sortKey(a);
  const [rb, kb] = sortKey(b);
  if (ra !== rb) return ra - rb;
  if (typeof ka === "number" && typeof kb === "number") return ka - kb;
  return String(ka).localeCompare(String(kb));
}

export function DataGrid({
  columns, rows, startIndex = 0, totalRows = null, omitted = [], storageKey,
  numbered = true, renderCell, origin = "table", table,
}: {
  columns: string[];
  rows: Row[];
  /** Absolute index of `rows[0]` in the table, so a row can name itself by its
   *  real position rather than by where it landed on this page. */
  startIndex?: number;
  totalRows?: number | null;
  /** Columns the server declined to read. They are listed in the row panel so an
   *  empty-looking row reads as "not read" rather than "not there". */
  omitted?: OmittedColumn[];
  /** Column widths and hidden columns persist under this key. Widths are worth
   *  remembering — you size a caption column once per table, not once per page. */
  storageKey?: string;
  numbered?: boolean;
  /** For the few columns a panel knows better than the grid does — a search
   *  distance wants four decimals, not three. Returning null takes the default. */
  renderCell?: (col: string, v: Cell) => React.ReactNode | null;
  /** What a row's number means. A page of a table numbers rows by where they are
   *  in the table; a query result numbers them by where they came in the answer,
   *  and calling the third hit "row 3 of 937,957" would be a claim about the
   *  table that nothing here established. */
  origin?: "table" | "result";
  /** The table these rows came from. Given it, the row panel can offer to read a
   *  heavy column it declined to read — one row at a time, and priced. Without it
   *  the panel still lists those columns; it just cannot fetch them. */
  table?: string;
}) {
  // Read once, at mount, rather than in an effect: the parent remounts this grid
  // per table, so "at mount" is exactly "when the table changed" — and restoring
  // through state-setting effects would render the default layout first and then
  // snap to the saved one.
  const saved = useMemo(() => remembered(storageKey), [storageKey]);
  const [hidden, setHidden] = useState<string[]>(saved.hidden);
  const [widths, setWidths] = useState<Record<string, number>>(saved.widths);
  const [wrap, setWrap] = useState(saved.wrap);
  const [sort, setSort] = useState<Sort>(null);
  const [sel, setSel] = useState<number | null>(null);
  const [pickCols, setPickCols] = useState(false);
  const [dragging, setDragging] = useState<
    { col: string; startX: number; startW: number } | null>(null);

  useEffect(() => {
    if (!storageKey || typeof window === "undefined") return;
    try {
      window.localStorage.setItem(
        `lancescope.grid.${storageKey}`,
        JSON.stringify({ widths, hidden, wrap }),
      );
    } catch {
      // Private mode, quota, a browser with storage off. Nothing to say.
    }
  }, [storageKey, widths, hidden, wrap]);

  const shown = useMemo(
    () => columns.filter((c) => !hidden.includes(c)),
    [columns, hidden],
  );

  // Row order is a list of indices into `rows`, never a copy of the rows, so a
  // selected row keeps its identity — and its real position — through a sort.
  const order = useMemo(() => {
    const ix = rows.map((_, i) => i);
    if (!sort) return ix;
    const sign = sort.dir === "asc" ? 1 : -1;
    return ix.sort((a, b) => sign * compare(rows[a][sort.col], rows[b][sort.col]));
  }, [rows, sort]);

  const move = useCallback((delta: number) => {
    setSel((cur) => {
      if (cur === null) return order[0] ?? null;
      const at = order.indexOf(cur);
      const next = order[Math.min(Math.max(at + delta, 0), order.length - 1)];
      return next ?? cur;
    });
  }, [order]);

  // Keyboard only takes over while a row is open. A grid that swallows the arrow
  // keys of a page you are only scrolling is a grid people fight.
  useEffect(() => {
    if (sel === null) return;
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement;
      if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) return;
      if (e.key === "Escape") { setSel(null); return; }
      if (e.key === "ArrowDown" || e.key === "j") { e.preventDefault(); move(1); }
      if (e.key === "ArrowUp" || e.key === "k") { e.preventDefault(); move(-1); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sel, move]);

  // A drag is an external system — the pointer — so it is subscribed to for as
  // long as one is in progress and torn down with it, cursor and text selection
  // included. Without those two the page selects the whole grid as you drag.
  useEffect(() => {
    if (!dragging) return;
    const { col, startX, startW } = dragging;
    const onMove = (e: MouseEvent) =>
      setWidths((w) => ({ ...w, [col]: Math.max(MIN_WIDTH, startW + e.clientX - startX) }));
    const onUp = () => setDragging(null);
    const body = document.body;
    const prev = { cursor: body.style.cursor, select: body.style.userSelect };
    body.style.cursor = "col-resize";
    body.style.userSelect = "none";
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      body.style.cursor = prev.cursor;
      body.style.userSelect = prev.select;
    };
  }, [dragging]);

  const toggleSort = (col: string) =>
    setSort((s) =>
      s?.col !== col ? { col, dir: "asc" }
      : s.dir === "asc" ? { col, dir: "desc" }
      : null);

  return (
    <>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <button
          className={`btn mono !h-[26px] !px-2.5 text-[10px] tracking-[0.14em] uppercase ${wrap ? "btn-on" : ""}`}
          onClick={() => setWrap((v) => !v)}
          title={wrap
            ? "Cells are showing their full text over several lines"
            : "Let long cells wrap instead of ending in an ellipsis"}
        >
          <Icon name="rows" size={12} />
          {wrap ? "wrapped" : "wrap"}
        </button>

        <div className="relative">
          <button
            className={`btn mono !h-[26px] !px-2.5 text-[10px] tracking-[0.14em] uppercase ${hidden.length ? "btn-on" : ""}`}
            onClick={() => setPickCols((v) => !v)}
            title="Choose which columns the grid shows"
          >
            <Icon name="schema" size={12} />
            columns
            {hidden.length > 0 && <span>· {shown.length}/{columns.length}</span>}
          </button>
          {pickCols && (
            <>
              <div className="fixed inset-0 z-30" onClick={() => setPickCols(false)} />
              <div className="panel absolute left-0 top-[30px] z-40 p-2 w-[240px]
                              max-h-[320px] overflow-y-auto shadow-lg">
                <div className="flex items-center justify-between px-1.5 pb-1.5 mb-1"
                     style={{ borderBottom: "1px solid var(--rule)" }}>
                  <span className="eyebrow">shown in grid</span>
                  <button className="mono text-[10px] text-[var(--haze)] hover:text-[var(--bright)]"
                          onClick={() => setHidden([])}>
                    all
                  </button>
                </div>
                {columns.map((c) => (
                  <label key={c}
                         className="flex items-center gap-2 px-1.5 py-1 mono text-[11px]
                                    text-[var(--body)] cursor-pointer hover:text-[var(--bright)]">
                    <input
                      type="checkbox"
                      checked={!hidden.includes(c)}
                      onChange={() => setHidden((h) =>
                        h.includes(c) ? h.filter((x) => x !== c) : [...h, c])}
                    />
                    <span className="truncate">{c}</span>
                  </label>
                ))}
                <p className="text-[10px] text-[var(--haze)] leading-relaxed px-1.5 pt-2 mt-1"
                   style={{ borderTop: "1px solid var(--rule)" }}>
                  Hiding a column only takes it off the grid. It was already read, and
                  the row panel still shows it.
                </p>
              </div>
            </>
          )}
        </div>

        {sort && (
          <button
            className="btn btn-on mono !h-[26px] !px-2.5 text-[10px] tracking-[0.14em] uppercase"
            onClick={() => setSort(null)}
            title="Sorting applies to the rows on this page only"
          >
            <Icon name="close" size={12} />
            {sort.col} {sort.dir}
          </button>
        )}

        <span className="mono text-[10px] text-[var(--haze)] ml-auto">
          click a row to open it{sel !== null ? " · ↑ ↓ to walk · esc to close" : ""}
        </span>
      </div>

      <div className="overflow-auto max-h-[64vh] rounded-sm"
           style={{ border: "1px solid var(--rule)" }}>
        <table className="w-full" style={{ tableLayout: "fixed" }}>
          <colgroup>
            {numbered && <col style={{ width: 52 }} />}
            {shown.map((c) => (
              <col key={c} style={{ width: widths[c] ?? DEFAULT_WIDTH }} />
            ))}
          </colgroup>
          <thead className="sticky top-0 z-20">
            <tr>
              {numbered && (
                <th className="eyebrow font-normal text-right px-3 py-2"
                    style={{ background: "var(--ink-2)", borderBottom: "1px solid var(--rule)" }}>
                  #
                </th>
              )}
              {shown.map((c) => (
                <th
                  key={c}
                  className="eyebrow font-normal text-left px-3 py-2 relative select-none"
                  style={{ background: "var(--ink-2)", borderBottom: "1px solid var(--rule)" }}
                >
                  <button
                    className="flex items-baseline gap-1 max-w-full hover:text-[var(--bright)]"
                    onClick={() => toggleSort(c)}
                    title={`${c} — sort the rows on this page`}
                  >
                    <span className="truncate">{c}</span>
                    {sort?.col === c && (
                      <span style={{ color: "var(--index)" }}>
                        {sort.dir === "asc" ? "↑" : "↓"}
                      </span>
                    )}
                  </button>
                  <span
                    role="separator"
                    aria-label={`resize ${c}`}
                    onMouseDown={(e) => {
                      e.preventDefault();
                      setDragging({ col: c, startX: e.clientX,
                                    startW: widths[c] ?? DEFAULT_WIDTH });
                    }}
                    onDoubleClick={() =>
                      setWidths((w) => { const n = { ...w }; delete n[c]; return n; })}
                    className="absolute top-0 right-0 h-full w-[7px] cursor-col-resize
                               hover:bg-[var(--rule)]"
                  />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {order.map((i) => {
              const r = rows[i];
              const on = sel === i;
              return (
                <tr
                  key={i}
                  onClick={() => setSel(on ? null : i)}
                  className="cursor-pointer"
                  style={{
                    borderBottom: "1px solid var(--hairline)",
                    background: on ? "rgb(var(--index-rgb) / 0.1)" : undefined,
                  }}
                >
                  {numbered && (
                    <td className="mono text-[11px] px-3 py-2 align-top text-right"
                        style={{ color: on ? "var(--index)" : "var(--dim)" }}>
                      {startIndex + i + 1}
                    </td>
                  )}
                  {shown.map((c) => (
                    <td
                      key={c}
                      className="mono text-[12px] px-3 py-2 align-top"
                      style={{ color: "var(--body)" }}
                      title={cellText(r[c])}
                    >
                      {/* The clamp lives on a child, never on the cell: both
                          `truncate` and `line-clamp` set a display, and a `<td>`
                          that stops being a table-cell takes the whole grid's
                          column alignment with it. Wrapped, but bounded — a
                          five-hundred-word caption would otherwise make one row
                          taller than the grid, and full length is what the row
                          panel is for. */}
                      <div className={wrap
                        ? "whitespace-pre-wrap break-words line-clamp-6"
                        : "truncate"}>
                        {renderCell?.(c, r[c]) ?? <CellView v={r[c]} />}
                      </div>
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {sort && (
        <p className="text-[11px] text-[var(--haze)] mt-2 leading-relaxed">
          Sorted on <span className="mono">{sort.col}</span> — these{" "}
          {rows.length} rows only. The other{" "}
          {totalRows !== null && totalRows > rows.length
            ? (totalRows - rows.length).toLocaleString()
            : "unread"}{" "}
          rows were never read, so this is the order of{" "}
          {origin === "table" ? "a page, not of a table" : "an answer, not of a table"}.
        </p>
      )}

      {sel !== null && rows[sel] && (
        <RowPanel
          row={rows[sel]}
          table={table}
          columns={columns}
          omitted={omitted}
          position={startIndex + sel + 1}
          of={origin === "table" ? totalRows : rows.length}
          noun={origin === "table" ? "row" : "result"}
          onClose={() => setSel(null)}
          onStep={move}
          atStart={order.indexOf(sel) === 0}
          atEnd={order.indexOf(sel) === order.length - 1}
        />
      )}
    </>
  );
}

/** One row, whole.
 *
 *  Every column at full length — including the ones hidden from the grid, because
 *  hiding is a grid decision and not a claim about the row — plus the names of the
 *  columns the server never read, so absence reads as absence rather than as null. */
function RowPanel({
  row, columns, omitted, position, of, noun, onClose, onStep, atStart, atEnd, table,
}: {
  row: Row;
  columns: string[];
  table?: string;
  omitted: OmittedColumn[];
  position: number;
  of: number | null;
  noun: string;
  onClose: () => void;
  onStep: (delta: number) => void;
  atStart: boolean;
  atEnd: boolean;
}) {
  const json = useMemo(
    () => JSON.stringify(Object.fromEntries(columns.map((c) => [c, row[c] ?? null])), null, 2),
    [row, columns],
  );

  // The page behind a drawer must not move: a wheel that lands anywhere but the
  // panel would otherwise scroll the grid out from under the row you are reading.
  useEffect(() => {
    const body = document.body;
    const prev = body.style.overflow;
    body.style.overflow = "hidden";
    return () => { body.style.overflow = prev; };
  }, []);

  return (
    <>
      <div className="fixed inset-0 z-40" style={{ background: "rgb(0 0 0 / 0.28)" }}
           onClick={onClose} />
      <aside
        className="fixed right-0 top-0 z-50 h-screen w-[min(560px,94vw)] flex flex-col"
        style={{ background: "var(--ink-2)", borderLeft: "1px solid var(--rule)" }}
      >
        <div className="flex items-center gap-2 px-5 py-3.5 shrink-0"
             style={{ borderBottom: "1px solid var(--rule)" }}>
          <span className="mono text-[12px] text-[var(--bright)]">
            {noun} {position.toLocaleString()}
            {of !== null && (
              <span className="text-[var(--haze)]"> of {of.toLocaleString()}</span>
            )}
          </span>
          <div className="ml-auto flex items-center gap-1.5">
            <CopyButton text={json} label="row json" />
            <button className="iconbtn !w-7 !h-7" disabled={atStart}
                    onClick={() => onStep(-1)} aria-label="previous row" data-tip="Previous row">
              <Icon name="chevronLeft" size={13} />
            </button>
            <button className="iconbtn !w-7 !h-7" disabled={atEnd}
                    onClick={() => onStep(1)} aria-label="next row" data-tip="Next row">
              <Icon name="chevronRight" size={13} />
            </button>
            <button className="iconbtn !w-7 !h-7" onClick={onClose}
                    aria-label="close row" data-tip="Close (esc)">
              <Icon name="close" size={13} />
            </button>
          </div>
        </div>

        <div className="overflow-y-auto px-5 py-4 flex-1">
          {columns.map((c) => (
            <Field key={c} name={c} v={row[c]}
                   table={table}
                   rowid={typeof row._rowid === "number" ? row._rowid : undefined} />
          ))}

          {omitted.length > 0 && (
            <>
              <div className="eyebrow mt-6 mb-2">Not read for this row</div>
              <div className="flex flex-wrap gap-2">
                {omitted.filter((o) => !readable(o, row, table)).map((o) => (
                  <span key={o.name} title={`${o.type} — ${o.reason}`}
                        className="mono text-[11px] px-2.5 py-1.5 rounded-sm"
                        style={{ border: "1px solid var(--rule)", color: "var(--haze)" }}>
                    {o.name}{o.vector_dim ? `[${o.vector_dim}]` : ""}
                  </span>
                ))}
              </div>
              <p className="text-[11px] text-[var(--haze)] leading-relaxed mt-2.5">
                These columns are empty here because opening a row costs nothing —
                the bytes are still on disk, and reading them is a decision you make.
              </p>

              {/* And here is where that decision gets made. A column of bytes is
                  offered a row at a time: the button says what reading it will
                  cost, and the caption says what it did. Nothing is fetched until
                  somebody asks — a panel that loaded every thumbnail as it opened
                  would undo the sentence directly above it. */}
              {omitted.filter((o) => readable(o, row, table)).map((o) => (
                <div key={o.name} className="mt-4">
                  <div className="eyebrow mb-1.5">{o.name}</div>
                  <CellMedia
                    // The row is part of its identity: stepping to the next row must
                    // drop the picture rather than carry it over under a new name.
                    key={`${row._rowid}:${o.name}`}
                    table={table as string}
                    column={o.name}
                    row={{ rowid: row._rowid as number }}
                    bytes={cellBytes(row[o.name])}
                  />
                </div>
              ))}
            </>
          )}
        </div>
      </aside>
    </>
  );
}

/** One column of one row. Long text gets height and stays selectable; the
 *  summaries the server sends for heavy columns stay summaries, and say what
 *  they are standing in for. */
/** Whether this omitted column is one the panel can actually go and read.
 *
 *  Bytes only, and only when there is a table to ask and a row id to ask about. A
 *  vector is left as a chip: fetching 768 floats to print them is a read that
 *  answers nothing you could not already see in the summary. */
function readable(o: OmittedColumn, row: Row, table?: string): boolean {
  return Boolean(table)
    && typeof row._rowid === "number"
    && o.vector_dim === null
    && /binary/i.test(o.type);
}

/** The size the server already reported for a heavy cell, when it reported one, so
 *  the button can price itself without a request of its own. */
function cellBytes(v: Cell | undefined): number | null {
  if (v && typeof v === "object" && "bytes" in v) return v.bytes;
  if (v && typeof v === "object" && "size_bytes" in v) return v.size_bytes ?? null;
  return null;
}

function Field({ name, v, table, rowid }: {
  name: string; v: Cell; table?: string; rowid?: number;
}) {
  const text = cellText(v);
  const heavy = typeof v === "object" && v !== null;
  return (
    <div className="py-2.5" style={{ borderBottom: "1px solid var(--hairline)" }}>
      <div className="flex items-baseline gap-2 mb-1">
        <span className="eyebrow truncate">{name}</span>
        {!heavy && v !== null && (
          <span className="ml-auto shrink-0"><CopyButton text={text} label="" /></span>
        )}
      </div>
      {v === null ? (
        <span className="mono text-[12px] text-[var(--dim)]">null</span>
      ) : heavy ? (
        <HeavyValue v={v} name={name} table={table} rowid={rowid} />
      ) : (
        <div className="mono text-[12px] leading-relaxed whitespace-pre-wrap break-words
                        max-h-[320px] overflow-y-auto"
             style={{ color: "var(--body)" }}>
          {text}
        </div>
      )}
    </div>
  );
}

function HeavyValue({ v, name, table, rowid }: {
  v: Cell & object; name?: string; table?: string; rowid?: number;
}) {
  if ("blob" in v) {
    const b = fmtBytes(v.size_bytes ?? 0);
    return (
      <div className="mono text-[12px] leading-relaxed" style={{ color: "var(--video)" }}>
        blob, {v.size_bytes === null ? "size unknown" : `${b.value} ${b.unit}`}
        {v.position !== null && (
          <span className="text-[var(--haze)]"> · at offset {v.position.toLocaleString()}</span>
        )}
        <div className="text-[11px] text-[var(--haze)] mt-1">
          Described from its Blob V2 descriptor. Opening this row did not read it.
        </div>
        {/* And then the offer, which is the other half of that sentence rather than a
            contradiction of it. The panel described the cell for nothing; reading it
            costs what the button says, and the button is the only thing that spends it.

            This used to be reachable only for `binary` columns, through the omitted-
            column list, because `readable()` tests the type for /binary/ and a Blob V2
            column's type is `extension<lance.blob.v2<BlobType>>`. A described blob is
            not an omitted column — it never went through that list at all — so the one
            kind of column this repository is an argument about was the one kind you
            could not open. */}
        {table && typeof rowid === "number" && name && (
          <CellMedia
            key={`${rowid}:${name}`}
            className="mt-2.5"
            table={table}
            column={name}
            row={{ rowid }}
            bytes={v.size_bytes ?? null}
          />
        )}
      </div>
    );
  }
  if ("vector_dim" in v) {
    return (
      <div className="mono text-[12px] leading-relaxed" style={{ color: "var(--haze)" }}>
        <div className="break-words">
          [{v.head.map((n) => n.toFixed(4)).join(", ")}
          {v.head.length < v.vector_dim ? ", …" : ""}]
        </div>
        <div className="text-[11px] mt-1">
          {v.head.length} of {v.vector_dim} dimensions. The rest were not read.
        </div>
      </div>
    );
  }
  const b = fmtBytes(v.bytes);
  return (
    <span className="mono text-[12px]" style={{ color: "var(--index)" }}>
      {b.value} {b.unit}
    </span>
  );
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  return (
    <button
      className="btn mono !h-[24px] !px-2 !gap-1.5 text-[10px] tracking-[0.14em] uppercase"
      onClick={async (e) => {
        e.stopPropagation();
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          timer.current = setTimeout(() => setDone(false), 1200);
        } catch {
          // No clipboard permission. The value is on screen and selectable.
        }
      }}
      title={`Copy ${label || "this value"}`}
    >
      <Icon name={done ? "check" : "external"} size={11} />
      {done ? "copied" : label}
    </button>
  );
}
