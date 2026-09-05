"use client";

/** The query workspace.
 *
 *  The console could describe a table and page through it. It could not answer the
 *  question people arrive with — why is this query slow — because nothing here ever
 *  ran a query on purpose.
 *
 *  Three things this shows that a generic query runner cannot, all of them from
 *  Lance rather than from us: which access path was chosen, what the query actually
 *  cost in bytes and IOs, and the script that reproduces it elsewhere. No model is
 *  involved in any of it. */

import { useCallback, useEffect, useRef, useState } from "react";
import Icon from "@/app/components/Icon";
import { AskForFilter } from "@/app/components/console/AskForFilter";
import { Cost, Empty, Eyebrow } from "@/app/components/console/atoms";
import { DataGrid } from "@/app/components/console/DataGrid";
import { FilterInput } from "@/app/components/console/FilterInput";
import {
  ApiError,
  type CompletionColumn, type Estimate, type FilterValidation,
  type QueryCapabilities, type QueryCapability, type QueryResult, type QuerySpec,
  explainQuery, getQueryCapabilities, getQueryCompletions, runQuery, validateFilter,
} from "@/app/lib/catalog";
import { fmtBytes } from "@/app/lib/api";
import BundleButton from "@/app/components/console/BundleButton";
import { download, toCsv, toJson } from "@/app/lib/export";
import {
  type TextSearchCapability, embedQuery, getTextSearchCapability,
} from "@/app/lib/ingest";
import {
  type StoredQuery, describeSpec, useQueryHistory, useSavedQueries,
} from "@/app/lib/queries";
import type { Capabilities } from "@/app/lib/settings";

const MODE_LABEL: Record<string, string> = {
  scan: "filter",
  fts: "full text",
  vector: "vector",
  hybrid: "hybrid",
};

const MODES = ["scan", "fts", "vector", "hybrid"] as const;

/** A placeholder written against this table's own columns.
 *
 *  It used to read `track = 'Go' and year = 2025`, which is the demo corpus. On any
 *  other table that is a worked example guaranteed to fail, offered at the exact
 *  moment somebody is trying to learn the syntax.
 */
function filterExample(columns: CompletionColumn[]): string {
  const faceted = columns.find((c) => c.kind === "string" && c.values.length > 0);
  if (faceted) return `${faceted.name} = ${faceted.values[0]}`;
  const num = columns.find((c) => c.kind === "number");
  if (num) return `${num.name} > 0`;
  const any = columns.find((c) => c.filterable);
  return any ? `${any.name} IS NOT NULL` : "a predicate over this table's columns";
}

export function QueryTab({ table, root, ai }: {
  table: string; root: string | null; ai: Capabilities | null;
}) {
  const [caps, setCaps] = useState<QueryCapabilities | null>(null);
  const [mode, setMode] = useState<QuerySpec["mode"]>("scan");
  const [filter, setFilter] = useState("");
  const [text, setText] = useState("");
  const [vectorColumn, setVectorColumn] = useState("");
  const [likeRow, setLikeRow] = useState("0");
  const [k, setK] = useState("10");
  const [limit, setLimit] = useState("25");
  const [prefilter, setPrefilter] = useState(true);
  // Heavy columns the reader has asked for by name. Empty is the default: a result
  // leaves the vectors on disk until somebody clicks one, and then says what it
  // cost to have them.
  const [expand, setExpand] = useState<string[]>([]);
  // Where the result on screen sits, and how wide the read that produced it was.
  // Held beside the result rather than read from the limit box, so editing that box
  // cannot silently re-describe a page that was read at another width.
  const [page, setPage] = useState({ offset: 0, limit: 25 });

  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Cancellation is client-side by necessity: Lance exposes no way to interrupt a
  // running scan, so this abandons the wait and says so rather than implying the
  // work stopped.
  const [aborter, setAborter] = useState<AbortController | null>(null);
  const [cancelled, setCancelled] = useState(false);
  // How long this console will wait, sent with the query. Not how long the query may
  // run: the server says so in its own words on a 408, and Lance would not honour a
  // deadline if we set one. Named state rather than a red error box, because nothing
  // is broken — the wait ran out, and the two read differently to whoever is looking.
  const [timeout, setTimeoutS] = useState("30");
  const [timedOut, setTimedOut] = useState<string | null>(null);
  const [saveName, setSaveName] = useState("");

  // Both lists are per database and live in the browser, beside recents and pins —
  // a query written against one database means nothing against another.
  const { history, record, clear } = useQueryHistory(root);
  const { saved, save, remove } = useSavedQueries(root);
  const [describe, setDescribe] = useState("");
  const describeRef = useRef<HTMLInputElement>(null);
  const [textSearch, setTextSearch] = useState<TextSearchCapability | null>(null);
  const [columns, setColumns] = useState<CompletionColumn[]>([]);
  // The verdict is stored with the predicate it describes. Keeping them together is
  // what makes "is this answer still about what is in the box" a comparison rather
  // than a second piece of state to remember to clear — and a stale count under an
  // edited filter is the one failure that would make this worse than saying nothing.
  const [check, setCheck] = useState<{ for: string; v: FilterValidation } | null>(null);
  // What a scan of this table would weigh, before it is run. Held with the mode it
  // describes, the same way the filter verdict is held with its predicate: a weight
  // shown under a vector query would be answering a question nobody asked.
  const [weight, setWeight] = useState<{ mode: string; est: Estimate | null } | null>(null);
  const [showPlan, setShowPlan] = useState(false);
  const [showRepro, setShowRepro] = useState(false);
  // Whether to read the first page on open. Undecided until the capability probes
  // settle, because a `?tab=query` deep link on a searchable table opens in vector
  // mode instead, and a scan fired underneath it would be a read nobody asked for.
  // A ref rather than state: it is a latch, and re-rendering to say "already fired"
  // would be a render that changes nothing on screen. `probed` is what wakes the
  // effect, and it moves whether the probe answered or failed.
  const browseOnOpen = useRef<boolean | null>(null);
  const [probed, setProbed] = useState(0);

  // Everything below is per table, and the parent remounts this on a table change
  // (`key={table}`) rather than resetting six pieces of state by hand — which is
  // also what stops a result from one table being shown under another's name.
  useEffect(() => {
    // Once per table, not once per keystroke: the columns and their facets are the
    // same for every predicate anyone types against it.
    getQueryCompletions(table)
      .then((c) => setColumns(c.columns))
      .catch(() => setColumns([]));
    getQueryCapabilities(table)
      .then((c) => {
        setCaps(c);
        const vector = c.capabilities.find((x) => x.mode === "vector");
        setVectorColumn(vector?.columns[0] ?? "");
      })
      .catch(() => setCaps(null));
    // Asked before the box is drawn, so a table whose vectors came from a model this
    // console cannot reproduce says so rather than offering an input that will
    // refuse whatever is typed into it.
    getTextSearchCapability(table)
      .then((cap) => {
        setTextSearch(cap);
        // Arriving from a finished build, with a table that can answer a typed
        // question: start in the mode that answers it and put the cursor in the box.
        // The alternative is a search screen whose search field is three clicks away.
        if (cap.available && typeof window !== "undefined"
            && new URLSearchParams(window.location.search).get("tab") === "query") {
          setMode("vector");
          browseOnOpen.current = false;
          requestAnimationFrame(() => describeRef.current?.focus());
        } else {
          browseOnOpen.current = true;
        }
      })
      .catch(() => { setTextSearch(null); browseOnOpen.current = true; })
      .finally(() => setProbed((n) => n + 1));
  }, [table]);

  // Whether the predicate parses, and what it matches, before anything is run.
  //
  // Debounced rather than per keystroke, and aborted when the text moves on, so a
  // slow count against a large table cannot land after the filter it described has
  // been edited.
  useEffect(() => {
    const text = filter.trim();
    if (!text) return;
    const controller = new AbortController();
    const t = setTimeout(() => {
      validateFilter(table, text, controller.signal)
        .then((v) => setCheck({ for: text, v }))
        .catch((e) => {
          // A failure is recorded against this text rather than dropped. Dropping it
          // leaves "checking…" under the box forever, which is the one reading of
          // this line that is never true.
          if (controller.signal.aborted) return;
          setCheck({ for: text, v: {
            valid: false,
            error: e instanceof ApiError ? e.message : "could not check this filter",
            filter: text, matched_rows: null, total_rows: null,
            read_bytes: 0, read_iops: 0,
          } });
        });
    }, 400);
    return () => { clearTimeout(t); controller.abort(); };
  }, [table, filter]);

  useEffect(() => {
    if (mode !== "scan") return;
    const controller = new AbortController();
    const t = setTimeout(() => {
      explainQuery(table, { mode: "scan", limit: 1 })
        .then((r) => { if (!controller.signal.aborted) setWeight({ mode, est: r.estimate }); })
        .catch(() => {});
    }, 400);
    return () => { clearTimeout(t); controller.abort(); };
  }, [table, mode]);

  const weighed = weight?.mode === mode ? weight.est : null;

  // Both derived rather than stored. An answer is shown when it is an answer about
  // the predicate currently in the box; anything else is still being checked.
  const asked = filter.trim();
  const verdict = check && check.for === asked ? check.v : null;
  const checking = asked !== "" && verdict === null;

  const capFor = (m: string): QueryCapability | undefined =>
    caps?.capabilities.find((c) => c.mode === m);

  /** Run what is in the form.
   *
   *  `over` is how paging and expanding a column re-run *this* query rather than
   *  starting a new one: everything else about the spec is read from the form, so
   *  page 3 of a filter is the same query with one number changed. A bare `run()`
   *  is a new question and starts at the first page — landing on page 3 of an
   *  answer you have not seen page 1 of is never what was meant. */
  const run = useCallback(async (
    over?: { offset?: number; expand?: string[]; limit?: number },
  ) => {
    const off = over?.offset ?? 0;
    const exp = over?.expand ?? expand;
    const wide = over?.limit ?? (Number(limit) || 25);
    const controller = new AbortController();
    setAborter(controller);
    setBusy(true); setError(null); setCancelled(false); setTimedOut(null);
    const wantsDescription =
      (mode === "vector" || mode === "hybrid") && describe.trim().length > 0;

    let queryVector: number[] | null = null;
    if (wantsDescription) {
      try {
        queryVector = (await embedQuery(table, describe.trim(), controller.signal)).vector;
      } catch (e) {
        setError(e instanceof ApiError ? e.message : String(e));
        setBusy(false); setAborter(null);
        return;
      }
    }

    const spec: QuerySpec = {
      mode,
      filter: filter.trim() || null,
      limit: wide,
      offset: off,
      // Null rather than absent when the box is empty or nonsense, so the server
      // falls back to its own default instead of being handed `NaN`.
      timeout_s: Number(timeout) > 0 ? Number(timeout) : null,
      expand: exp.length ? exp : null,
      ...(mode === "fts" || mode === "hybrid" ? { text } : {}),
      ...(mode === "vector" || mode === "hybrid"
        ? {
            vector_column: vectorColumn,
            k: Number(k) || 10,
            prefilter,
            // A described query and "rows like row N" are the same search with a
            // different source for the vector, so only one of them is sent.
            ...(queryVector ? { vector: queryVector } : { like_row: Number(likeRow) || 0 }),
          }
        : {}),
    };
    try {
      const r = await runQuery(table, spec, controller.signal);
      setResult(r);
      setPage({ offset: off, limit: wide });
      setExpand(exp);
      record(table, spec, { read_bytes: r.read_bytes, ms: r.ms,
                            returned: r.returned, version: r.version });
    } catch (e) {
      setResult(null);
      if (e instanceof DOMException && e.name === "AbortError") {
        setCancelled(true);
      } else if (e instanceof ApiError && e.status === 408) {
        // Not an error. The wait ran out and the scan is still going, which is a
        // different thing to say and a different thing to do about it.
        setTimedOut(e.message);
      } else {
        // A query someone typed is theirs to fix; say what Lance said about it.
        setError(e instanceof ApiError ? e.message : "the query could not be run");
      }
    } finally {
      setBusy(false);
      setAborter(null);
    }
  }, [table, mode, filter, limit, text, vectorColumn, likeRow, k, prefilter, describe,
      expand, record, timeout]);

  // Opening the panel reads the first page. The console used to answer "pick a
  // mode and press Run" to the question "what is in this table", which is a click
  // and a decision in front of the most common thing anyone does here.
  useEffect(() => {
    if (!probed || browseOnOpen.current !== true) return;
    browseOnOpen.current = false;    // before the await, so a re-render cannot re-fire it
    run();
  }, [probed, run]);

  /** Put a stored query back in the form. It is not run: a saved query is a
   *  question, and running it is still the reader's decision — especially since the
   *  cost recorded beside it describes a past run against a version that may have
   *  moved. */
  const load = useCallback((q: StoredQuery) => {
    setMode(q.spec.mode);
    setFilter(q.spec.filter ?? "");
    setText(q.spec.text ?? "");
    setVectorColumn(q.spec.vector_column ?? "");
    setLikeRow(String(q.spec.like_row ?? 0));
    setK(String(q.spec.k ?? 10));
    setLimit(String(q.spec.limit ?? 25));
    setExpand(q.spec.expand ?? []);
    setResult(null);
    setError(null);
  }, []);

  const currentSpec = (): QuerySpec => ({
    mode,
    filter: filter.trim() || null,
    limit: Number(limit) || 25,
    expand: expand.length ? expand : null,
    ...(mode === "fts" || mode === "hybrid" ? { text } : {}),
    ...(mode === "vector" || mode === "hybrid"
      ? { vector_column: vectorColumn, like_row: Number(likeRow) || 0,
          k: Number(k) || 10, prefilter }
      : {}),
  });

  const active = capFor(mode);

  return (
    <>
      {/* A question in words, drafting into the box below rather than running.
          Scan only: the other three modes already take a sentence, and translating
          English into a predicate to sit beside a vector search would be answering
          a question with the wrong half of the form. */}
      {ai?.available && mode === "scan" && (
        <AskForFilter
          table={table}
          model={ai.models_by_role.fast.id}
          example={filterExample(columns)}
          onDraft={setFilter}
        />
      )}

      <div className="seg mb-4 flex-wrap">
        {MODES.map((m) => {
          const c = capFor(m);
          return (
            <button
              key={m}
              onClick={() => c?.available && setMode(m)}
              data-on={mode === m}
              disabled={!c?.available}
              title={c?.reason}
              className="mono !px-3.5 text-[10px] tracking-[0.14em] uppercase"
            >
              {MODE_LABEL[m]}
            </button>
          );
        })}
      </div>

      {/* Why a mode is unavailable, or what it will cost when it is. Never a
          disabled control with no explanation. */}
      {active && (
        <p className="text-[12px] text-[var(--haze)] leading-relaxed mb-4 max-w-[70ch]">
          {active.reason}
        </p>
      )}

      <div className="flex flex-wrap items-end gap-2 mb-4">
        {(mode === "fts" || mode === "hybrid") && (
          <Field label="search for" grow>
            <input className="qin" value={text} onChange={(e) => setText(e.target.value)}
                   onKeyDown={(e) => e.key === "Enter" && run()}
                   placeholder="kubernetes" />
          </Field>
        )}

        {(mode === "vector" || mode === "hybrid") && (
          <>
            <Field label="column">
              <select className="qin" value={vectorColumn}
                      onChange={(e) => setVectorColumn(e.target.value)}>
                {(capFor("vector")?.columns ?? []).map((c) =>
                  <option key={c} value={c}>{c}</option>)}
              </select>
            </Field>
            {/* Two ways to get a query vector. Describing one needs the model that
                built the column; a row's own vector needs nothing and cannot be
                wrong about which model that was. */}
            {textSearch?.available && (
              <Field label="describe what you want" grow>
                <input className="qin" ref={describeRef} value={describe}
                       placeholder={`in the words of ${textSearch.space?.model ?? "this table's model"}`}
                       onChange={(e) => setDescribe(e.target.value)} />
              </Field>
            )}
            <Field label={describe.trim() ? "rows like row (unused)" : "rows like row"}>
              <input className="qin" value={likeRow} inputMode="numeric"
                     disabled={describe.trim().length > 0}
                     onChange={(e) => setLikeRow(e.target.value)} />
            </Field>
            <Field label="k">
              <input className="qin" value={k} inputMode="numeric"
                     onChange={(e) => setK(e.target.value)} />
            </Field>
          </>
        )}

        {(mode === "vector" || mode === "hybrid") && textSearch
          && !textSearch.available && (
          <p className="text-[11px] text-[var(--haze)] leading-relaxed basis-full">
            {textSearch.reason}
          </p>
        )}

        <Field label={mode === "vector" ? "filter (applied before search)" : "filter"} grow>
          <FilterInput
            value={filter}
            onChange={setFilter}
            onEnter={() => run()}
            columns={columns}
            placeholder={filterExample(columns)}
          />
        </Field>

        {mode === "scan" || mode === "fts" ? (
          <Field label="limit">
            <input className="qin" value={limit} inputMode="numeric"
                   onChange={(e) => setLimit(e.target.value)} />
          </Field>
        ) : null}

        <Field label="wait">
          <input className="qin !w-[70px]" value={timeout} inputMode="numeric"
                 title="How long this console waits, in seconds. The scan itself cannot be interrupted."
                 onChange={(e) => setTimeoutS(e.target.value)} />
        </Field>

        <button className="btn btn-accent mono text-[10px] tracking-[0.14em] uppercase"
                onClick={() => run()} disabled={busy}>
          <Icon name="search" size={14} />
          {busy ? "running…" : "Run"}
        </button>
        {busy && aborter && (
          <button className="btn mono text-[10px] tracking-[0.14em] uppercase"
                  onClick={() => aborter.abort()}>
            <Icon name="close" size={14} />
            Stop waiting
          </button>
        )}
      </div>

      {mode === "scan" && weighed && (
        <div className="mono text-[11px] mb-4 flex items-baseline gap-2 flex-wrap">
          <span className="text-[var(--haze)]">
            a full pass over this table weighs{" "}
            <span style={{ color: "var(--index)" }}>
              {fmtBytes(Math.max(weighed.bytes, weighed.floor_bytes)).value}{" "}
              {fmtBytes(Math.max(weighed.bytes, weighed.floor_bytes)).unit}
            </span>
          </span>
          <span className="text-[var(--dim)]">
            · weighed from the footers, so it is true for any reader — not only this one
          </span>
        </div>
      )}

      {filter.trim() && (
        <div className="mono text-[11px] mb-4 flex items-baseline gap-2 flex-wrap">
          {checking && <span className="text-[var(--dim)]">checking…</span>}
          {verdict?.valid && verdict.matched_rows !== null && (
            <>
              <span style={{ color: verdict.matched_rows === 0 ? "var(--video)" : "var(--index)" }}>
                matches {verdict.matched_rows.toLocaleString()} of{" "}
                {(verdict.total_rows ?? 0).toLocaleString()} rows
              </span>
              <span className="text-[var(--dim)]">
                · counted for {fmtBytes(verdict.read_bytes).value}{" "}
                {fmtBytes(verdict.read_bytes).unit}
              </span>
              {verdict.matched_rows === 0 && (
                <span className="text-[var(--haze)]">
                  — the filter is valid, so this is what the table says, not a mistake
                  in the predicate
                </span>
              )}
            </>
          )}
          {verdict && !verdict.valid && (
            <span style={{ color: "var(--video)" }}>{verdict.error}</span>
          )}
        </div>
      )}

      {(mode === "vector" || mode === "hybrid") && (
        <label className="flex items-center gap-2 mb-4 text-[12px] text-[var(--haze)]">
          <input type="checkbox" checked={prefilter}
                 onChange={(e) => setPrefilter(e.target.checked)} />
          apply the filter before the search rather than after — fewer candidates,
          and a different answer
        </label>
      )}

      {cancelled && (
        <div className="text-[12px] leading-relaxed px-3.5 py-3 rounded-sm mb-4"
             style={{ background: "rgb(var(--index-rgb) / 0.08)",
                      border: "1px solid rgb(var(--index-rgb) / 0.35)",
                      color: "var(--body)" }}>
          <span className="mono" style={{ color: "var(--index)" }}>stopped waiting.</span>{" "}
          The scan is still running on the server until it finishes — Lance offers no
          way to interrupt one, so this abandoned the wait, not the work.
        </div>
      )}

      {timedOut && (
        <div className="text-[12px] leading-relaxed px-3.5 py-3 rounded-sm mb-4"
             style={{ background: "rgb(var(--index-rgb) / 0.08)",
                      border: "1px solid rgb(var(--index-rgb) / 0.35)",
                      color: "var(--body)" }}>
          <span className="mono" style={{ color: "var(--index)" }}>timed out.</span>{" "}
          {timedOut} Raise the wait above, or narrow the query — the weight under the
          filter box says what a full pass costs before you spend it again.
        </div>
      )}

      {error && (
        <div className="mono flex items-center gap-2.5 text-[12px] px-3.5 py-3 rounded-sm mb-4"
             style={{ background: "rgb(var(--video-rgb) / 0.12)",
                      border: "1px solid rgb(var(--video-rgb) / 0.4)",
                      color: "var(--video)" }}>
          <Icon name="warning" size={15} />
          {error}
        </div>
      )}

      {result?.stale && (
        <div className="text-[12px] leading-relaxed px-3.5 py-3 rounded-sm mb-4"
             style={{ background: "rgb(var(--video-rgb) / 0.08)",
                      border: "1px solid rgb(var(--video-rgb) / 0.35)",
                      color: "var(--body)" }}>
          <span className="mono" style={{ color: "var(--video)" }}>
            this describes v{result.version}.
          </span>{" "}
          The table has moved to v{result.latest_version} since. Everything below is
          still true of the version it was read from, and no longer describes the
          table as it is now — run it again to catch up.
        </div>
      )}

      {result && (
        <>
          <Diagnosis d={result} onPlan={() => setShowPlan((v) => !v)} planOpen={showPlan}
                     onRepro={() => setShowRepro((v) => !v)} reproOpen={showRepro} />

          {showPlan && (
            <pre className="mono text-[10px] leading-relaxed p-4 rounded-sm mb-4
                            overflow-x-auto whitespace-pre"
                 style={{ background: "var(--ink-3)", border: "1px solid var(--rule)",
                          color: "var(--body)" }}>
              {result.plan.text}
            </pre>
          )}

          {/* Heavy columns this result declined to read, each priced by clicking it.
              They sit above the grid because that is where the missing columns are,
              and reading one re-runs this same page rather than a new query. */}
          {(result.omitted_columns.length > 0 || expand.length > 0) && (
            <div className="flex flex-wrap items-center gap-2 mb-4">
              {/* Not "not read": one of these chips names a column that *was*, and
                  a label contradicting the chip under it is the one mistake a
                  console about honest byte accounting cannot make. The chip says
                  which — `+` to read it, `×` to stop. */}
              <span className="eyebrow">Heavy columns</span>
              {result.omitted_columns.map((c) => (
                <button
                  key={c.name}
                  onClick={() => run({ offset: page.offset, expand: [...expand, c.name] })}
                  title={`${c.type} — click to read it and see what it costs`}
                  className="btn mono !h-[26px] !px-2.5 text-[11px]"
                >
                  <Icon name="plus" size={12} />
                  {c.name}{c.vector_dim ? `[${c.vector_dim}]` : ""}
                </button>
              ))}
              {expand.map((c) => (
                <button
                  key={c}
                  onClick={() => run({ offset: page.offset,
                                       expand: expand.filter((x) => x !== c) })}
                  className="btn mono !h-[26px] !px-2.5 text-[11px]"
                  style={{ borderColor: "var(--index)", color: "var(--index)",
                           background: "rgb(var(--index-rgb) / 0.09)" }}
                >
                  <Icon name="close" size={12} />
                  {c}
                </button>
              ))}
            </div>
          )}

          {showRepro && (
            <pre className="mono text-[10px] leading-relaxed p-4 rounded-sm mb-4
                            overflow-x-auto whitespace-pre"
                 style={{ background: "var(--ink-3)", border: "1px solid var(--rule)",
                          color: "var(--body)" }}>
              {result.reproduction}
            </pre>
          )}

          {result.returned === 0 ? (
            <Empty>No rows matched. The query ran; the answer is empty.</Empty>
          ) : (
            <DataGrid
              key={`${table}:${result.columns.join(",")}`}
              storageKey={`query.${table}`}
              table={table}
              columns={result.columns}
              rows={result.rows}
              totalRows={result.total_rows}
              omitted={result.omitted_columns}
              origin="result"
              renderCell={(c, v) =>
                typeof v === "number" && (c === "_distance" || c === "_score")
                  ? v.toFixed(4)
                  : null}
            />
          )}

          {result.mode === "scan" && result.total_rows !== null && (
            <Pager total={result.total_rows} returned={result.returned} page={page}
                   busy={busy} limit={limit}
                   onLimit={(n) => { setLimit(String(n)); run({ offset: 0, limit: n }); }}
                   onGo={(offset) => run({ offset })} />
          )}

          {/* Taking what is on screen with you. Directly under the rows it exports,
              because that is where you are when you decide to — it used to sit above
              the diagnosis, three scrolls from the grid it describes. */}
          <div className="flex flex-wrap items-center gap-2 mt-4">
            <span className="eyebrow">
              {result.mode === "scan" && result.total_rows !== null
                ? "this page" : "this result"}
            </span>
            <button className="btn mono !h-[26px] !px-2.5 text-[10px] tracking-[0.14em] uppercase"
                    onClick={() => download(`${table}-${page.offset}.csv`,
                                            toCsv(result.columns, result.rows),
                                            "text/csv")}>
              <Icon name="external" size={12} />csv
            </button>
            <button className="btn mono !h-[26px] !px-2.5 text-[10px] tracking-[0.14em] uppercase"
                    onClick={() => download(`${table}-${page.offset}.json`,
                                            toJson(result.columns, result.rows),
                                            "application/json")}>
              <Icon name="external" size={12} />json
            </button>
            {/* Which is only true while nothing has been expanded. Saying it
                anyway would put the console's central claim on a row it just
                stopped being true of. */}
            <span className="mono text-[10px] text-[var(--haze)]">
              the {result.returned} rows on screen — {expand.length === 0
                ? "heavy columns were never read, and export"
                : `${expand.join(", ")} was read, and every heavy column exports`}{" "}
              as the summaries shown here
            </span>
          </div>
          {/* The diagnosis, not the rows. Directly under the thing it describes, and
              separate from the csv/json above because those two export what is on
              screen and this exports why it looks like that. */}
          <BundleButton table={table} spec={currentSpec()}
                        saved={saved.filter((q) => q.table === table)}
                        note="this query, its plan, what it cost, and everything the findings say about the table" />

          <div className="flex flex-wrap items-center gap-2 mt-4">
            <span className="eyebrow">save as</span>
            <input className="qin w-[220px]" value={saveName}
                   onChange={(e) => setSaveName(e.target.value)}
                   onKeyDown={(e) => {
                     if (e.key === "Enter" && saveName.trim()) {
                       save(saveName.trim(), table, currentSpec());
                       setSaveName("");
                     }
                   }}
                   placeholder="what this query answers" />
            <button className="btn mono !h-[26px] !px-2.5 text-[10px] tracking-[0.14em] uppercase"
                    disabled={!saveName.trim()}
                    onClick={() => { save(saveName.trim(), table, currentSpec()); setSaveName(""); }}>
              <Icon name="check" size={12} />save
            </button>
          </div>

        </>
      )}

      {!result && !error && !busy && !cancelled && (
        <Empty>Run a query to see what it costs and which path Lance takes.</Empty>
      )}

      <QueryList title="Saved" queries={saved.filter((q) => q.table === table)}
                 onLoad={load} onRemove={remove} />
      <QueryList title="Recent" queries={history.filter((q) => q.table === table)}
                 onLoad={load} onClear={clear} />
    </>
  );
}

/** A stored query reads as what it asks and what it cost last time — not as a spec.
 *  Clicking one loads it into the form rather than running it: the recorded cost
 *  describes a past run, and deciding to spend it again is the reader's. */
function QueryList({ title, queries, onLoad, onRemove, onClear }: {
  title: string;
  queries: StoredQuery[];
  onLoad: (q: StoredQuery) => void;
  onRemove?: (id: string) => void;
  onClear?: () => void;
}) {
  if (!queries.length) return null;
  return (
    <div className="mt-7">
      <div className="flex items-center gap-3 mb-2">
        <Eyebrow>{title}</Eyebrow>
        {onClear && (
          <button className="mono text-[10px] text-[var(--haze)] hover:text-[var(--bright)]"
                  onClick={onClear}>
            clear
          </button>
        )}
      </div>
      <div className="space-y-1">
        {queries.map((q) => (
          <div key={q.id} className="flex items-center gap-3 px-3 py-2 rounded-sm border"
               style={{ borderColor: "var(--rule)" }}>
            <button className="text-left min-w-0 flex-1" onClick={() => onLoad(q)}>
              <div className="mono text-[12px] text-[var(--bright)] truncate">
                {q.name ?? describeSpec(q.spec)}
              </div>
              <div className="mono text-[10px] text-[var(--haze)] truncate">
                {q.spec.mode}
                {q.name ? ` · ${describeSpec(q.spec)}` : ""}
                {q.last && ` · last run ${fmtBytes(q.last.read_bytes).value} `
                  + `${fmtBytes(q.last.read_bytes).unit}, ${q.last.returned} rows, `
                  + `v${q.last.version}`}
              </div>
            </button>
            {onRemove && (
              <button className="mono text-[10px] text-[var(--haze)] hover:text-[var(--video)]"
                      onClick={() => onRemove(q.id)}>
                remove
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/** What just happened, in the order someone diagnosing would ask it: which path,
 *  how long, how many bytes. */
function Diagnosis({ d, onPlan, planOpen, onRepro, reproOpen }: {
  d: QueryResult;
  onPlan: () => void; planOpen: boolean;
  onRepro: () => void; reproOpen: boolean;
}) {
  const bytes = fmtBytes(d.read_bytes);
  const heavy = d.read_bytes > 1_000_000;
  return (
    <div className="rounded-sm border p-4 mb-4"
         style={{ borderColor: "var(--rule)", background: "var(--ink-3)" }}>
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
        <Stat label="returned" value={`${d.returned}${d.total_rows !== null ? ` of ${d.total_rows.toLocaleString()}` : ""}`} />
        <Stat label="time" value={`${d.ms} ms`} />
        <Stat label="read" value={`${bytes.value} ${bytes.unit}`}
              tone={heavy ? "video" : "index"} />
        <Stat label="ios" value={String(d.read_iops)} />
        {d.plan.fragments !== null && <Stat label="fragments" value={String(d.plan.fragments)} />}
      </div>

      {d.legs.length > 0 && (
        <div className="mt-3 space-y-1.5 pl-3"
             style={{ borderLeft: "2px solid var(--rule)" }}>
          {d.legs.map((leg) => {
            const b = fmtBytes(leg.read_bytes);
            const paths = leg.plan.paths.map((p) => p.name).join(", ") || "plain read";
            return (
              <div key={leg.mode} className="mono text-[11px] text-[var(--haze)]">
                <span style={{ color: "var(--bright)" }}>{leg.mode}</span>{" "}
                {leg.returned} rows · {leg.ms} ms ·{" "}
                <span style={{ color: leg.read_bytes > 1_000_000 ? "var(--video)" : "var(--index)" }}>
                  {b.value} {b.unit}
                </span>{" "}
                · {paths}
              </div>
            );
          })}
          <p className="text-[11px] text-[var(--body)] leading-relaxed pt-1">
            Two searches, fused by rank rather than by score — BM25 relevance and a
            vector distance are different quantities, and one of them is better when
            it is larger.
          </p>
        </div>
      )}

      {d.plan.paths.length > 0 ? (
        <div className="mt-3 space-y-1.5">
          {d.plan.paths.map((p) => (
            <div key={p.operator} className="text-[12px] leading-relaxed">
              <span className="mono" style={{ color: "var(--index)" }}>{p.name}</span>
              <span className="text-[var(--body)]"> — {p.meaning}</span>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-[12px] text-[var(--haze)] mt-3">
          A plain read. No index was involved and none was needed.
        </p>
      )}

      {d.plan.pushed_down_filter && (
        <p className="text-[12px] text-[var(--body)] mt-2 leading-relaxed">
          Filter pushed into the scan:{" "}
          <span className="mono text-[var(--bright)]">{d.plan.pushed_down_filter}</span>
          {" "}— rows were rejected while reading rather than after.
        </p>
      )}

      <div className="flex items-center gap-2 mt-3.5">
        <button className="btn mono !h-[26px] !px-2.5 text-[10px] tracking-[0.14em] uppercase"
                onClick={onPlan}>
          <Icon name={planOpen ? "close" : "plus"} size={12} />
          plan
        </button>
        <button className="btn mono !h-[26px] !px-2.5 text-[10px] tracking-[0.14em] uppercase"
                onClick={onRepro}>
          <Icon name={reproOpen ? "close" : "plus"} size={12} />
          python
        </button>
        <span className="ml-auto"><Cost bytes={d.read_bytes} iops={d.read_iops} label="this query" /></span>
      </div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: "index" | "video" }) {
  return (
    <div>
      <Eyebrow>{label}</Eyebrow>
      <div className="mono text-[15px]"
           style={{ color: tone ? `var(--${tone})` : "var(--bright)" }}>
        {value}
      </div>
    </div>
  );
}

/** Paging, for the one mode that has pages.
 *
 *  A top-k search returns k rows by construction, so offering a page 2 there would
 *  promise rows that do not exist behind the ones on screen — which is why this is
 *  rendered only for a scan with a real total. */
function Pager({ total, returned, page, busy, limit, onLimit, onGo }: {
  total: number;
  returned: number;
  page: { offset: number; limit: number };
  busy: boolean;
  limit: string;
  onLimit: (n: number) => void;
  onGo: (offset: number) => void;
}) {
  const last = page.offset + returned >= total;
  return (
    <div className="flex flex-wrap items-center gap-3 mt-5">
      <span className="mono text-[11px] text-[var(--haze)]">
        {total === 0
          ? "0 rows"
          : `${(page.offset + 1).toLocaleString()}–`
            + `${(page.offset + returned).toLocaleString()} of `
            + `${total.toLocaleString()}`}
      </span>

      {/* How many rows a page read is, stated as the read it is. Twenty-five at a
          time is a defensible default and a poor way to look for something; four
          hundred is the same read, four hundred rows wide. */}
      <label className="flex items-center gap-1.5 mono text-[11px] text-[var(--haze)]
                        whitespace-nowrap">
        <span>rows per read</span>
        <select
          className="qin !h-[26px] !py-0 !px-1.5 !text-[11px] w-[68px]"
          value={limit}
          onChange={(e) => onLimit(Number(e.target.value))}
        >
          {[25, 50, 100, 250, 500].map((n) => (
            <option key={n} value={String(n)}>{n}</option>
          ))}
        </select>
      </label>

      <div className="flex gap-2 ml-auto">
        <PageBtn disabled={busy || page.offset === 0} onClick={() => onGo(0)}>
          first
        </PageBtn>
        <PageBtn disabled={busy || page.offset === 0}
                 onClick={() => onGo(Math.max(0, page.offset - page.limit))}>
          <Icon name="chevronLeft" size={13} />
          prev
        </PageBtn>
        <PageBtn disabled={busy || last}
                 onClick={() => onGo(page.offset + page.limit)}>
          next
          <Icon name="chevronRight" size={13} />
        </PageBtn>
        <PageBtn disabled={busy || last}
                 onClick={() =>
                   onGo(Math.max(0, (Math.ceil(total / page.limit) - 1) * page.limit))}>
          last
        </PageBtn>
      </div>
    </div>
  );
}

function PageBtn({ disabled, onClick, children }: {
  disabled: boolean; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <button
      disabled={disabled}
      onClick={onClick}
      className="btn mono !h-[26px] !px-2.5 !gap-1.5 text-[11px]"
    >
      {children}
    </button>
  );
}

function Field({ label, grow, children }: {
  label: string; grow?: boolean; children: React.ReactNode;
}) {
  return (
    <label className={grow ? "flex-1 min-w-[220px]" : "w-[130px]"}>
      <span className="eyebrow block mb-1.5">{label}</span>
      {children}
    </label>
  );
}
