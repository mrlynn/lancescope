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

import { useCallback, useEffect, useState } from "react";
import Icon from "@/app/components/Icon";
import { Cost, Empty, Eyebrow, Td, Th } from "@/app/components/console/atoms";
import { CellView } from "@/app/components/console/tabs";
import {
  ApiError,
  type QueryCapabilities, type QueryCapability, type QueryResult, type QuerySpec,
  getQueryCapabilities, runQuery,
} from "@/app/lib/catalog";
import { fmtBytes } from "@/app/lib/api";

const MODE_LABEL: Record<string, string> = {
  scan: "filter",
  fts: "full text",
  vector: "vector",
  hybrid: "hybrid",
};

const MODES = ["scan", "fts", "vector", "hybrid"] as const;

export function QueryTab({ table }: { table: string }) {
  const [caps, setCaps] = useState<QueryCapabilities | null>(null);
  const [mode, setMode] = useState<QuerySpec["mode"]>("scan");
  const [filter, setFilter] = useState("");
  const [text, setText] = useState("");
  const [vectorColumn, setVectorColumn] = useState("");
  const [likeRow, setLikeRow] = useState("0");
  const [k, setK] = useState("10");
  const [limit, setLimit] = useState("25");
  const [prefilter, setPrefilter] = useState(true);

  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Cancellation is client-side by necessity: Lance exposes no way to interrupt a
  // running scan, so this abandons the wait and says so rather than implying the
  // work stopped.
  const [aborter, setAborter] = useState<AbortController | null>(null);
  const [cancelled, setCancelled] = useState(false);
  const [showPlan, setShowPlan] = useState(false);
  const [showRepro, setShowRepro] = useState(false);

  // Everything below is per table, and the parent remounts this on a table change
  // (`key={table}`) rather than resetting six pieces of state by hand — which is
  // also what stops a result from one table being shown under another's name.
  useEffect(() => {
    getQueryCapabilities(table)
      .then((c) => {
        setCaps(c);
        const vector = c.capabilities.find((x) => x.mode === "vector");
        setVectorColumn(vector?.columns[0] ?? "");
      })
      .catch(() => setCaps(null));
  }, [table]);

  const capFor = (m: string): QueryCapability | undefined =>
    caps?.capabilities.find((c) => c.mode === m);

  const run = useCallback(async () => {
    const controller = new AbortController();
    setAborter(controller);
    setBusy(true); setError(null); setCancelled(false);
    const spec: QuerySpec = {
      mode,
      filter: filter.trim() || null,
      limit: Number(limit) || 25,
      ...(mode === "fts" || mode === "hybrid" ? { text } : {}),
      ...(mode === "vector" || mode === "hybrid"
        ? { vector_column: vectorColumn, like_row: Number(likeRow) || 0,
            k: Number(k) || 10, prefilter }
        : {}),
    };
    try {
      setResult(await runQuery(table, spec, controller.signal));
    } catch (e) {
      setResult(null);
      if (e instanceof DOMException && e.name === "AbortError") {
        setCancelled(true);
      } else {
        // A query someone typed is theirs to fix; say what Lance said about it.
        setError(e instanceof ApiError ? e.message : "the query could not be run");
      }
    } finally {
      setBusy(false);
      setAborter(null);
    }
  }, [table, mode, filter, limit, text, vectorColumn, likeRow, k, prefilter]);

  const active = capFor(mode);

  return (
    <>
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
            {/* Searching by a row's own vector needs no embedding model, and cannot
                be wrong about which model produced the column. */}
            <Field label="rows like row">
              <input className="qin" value={likeRow} inputMode="numeric"
                     onChange={(e) => setLikeRow(e.target.value)} />
            </Field>
            <Field label="k">
              <input className="qin" value={k} inputMode="numeric"
                     onChange={(e) => setK(e.target.value)} />
            </Field>
          </>
        )}

        <Field label={mode === "vector" ? "filter (applied before search)" : "filter"} grow>
          <input className="qin mono" value={filter}
                 onChange={(e) => setFilter(e.target.value)}
                 onKeyDown={(e) => e.key === "Enter" && run()}
                 placeholder="track = 'Go' and year = 2025" />
        </Field>

        {mode === "scan" || mode === "fts" ? (
          <Field label="limit">
            <input className="qin" value={limit} inputMode="numeric"
                   onChange={(e) => setLimit(e.target.value)} />
          </Field>
        ) : null}

        <button className="btn btn-accent mono text-[10px] tracking-[0.14em] uppercase"
                onClick={run} disabled={busy}>
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
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr>{result.columns.map((c) => <Th key={c}>{c}</Th>)}</tr>
                </thead>
                <tbody>
                  {result.rows.map((r, i) => (
                    <tr key={i} style={{ borderBottom: "1px solid var(--hairline)" }}>
                      {result.columns.map((c) => (
                        <Td key={c} className="max-w-[280px] truncate">
                          {typeof r[c] === "number" && (c === "_distance" || c === "_score")
                            ? (r[c] as number).toFixed(4)
                            : <CellView v={r[c]} />}
                        </Td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {result.omitted_columns.length > 0 && (
            <p className="text-[11px] text-[var(--haze)] mt-4 leading-relaxed">
              Not read:{" "}
              <span className="mono">
                {result.omitted_columns.map((c) => c.name).join(", ")}
              </span>
              . Heavy columns stay out of a query result — that is where the bytes
              would have gone.
            </p>
          )}
        </>
      )}

      {!result && !error && !busy && (
        <Empty>Run a query to see what it costs and which path Lance takes.</Empty>
      )}
    </>
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
