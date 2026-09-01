"use client";

import { useState } from "react";
import Icon from "@/app/components/Icon";
import { Bytes, Caveat, Cost, Empty, Eyebrow, Td, Th, fmtWhen } from "@/app/components/console/atoms";
import { fmtBytes } from "@/app/lib/api";
import type {
  Cell, Fragments, Indices, Rows, TableDetail, Versions,
} from "@/app/lib/catalog";
import {
  type Capabilities, type FilterDraft, askForFilter,
} from "@/app/lib/settings";

// ------------------------------------------------------------------- schema

export function SchemaTab({ d }: { d: TableDetail }) {
  const { blob_bytes, meta_bytes, ratio, files } = d.on_disk;
  const total = Math.max(blob_bytes + meta_bytes, 1);
  const metaPct = (meta_bytes / total) * 100;

  return (
    <>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <Stat label="rows" value={d.rows.toLocaleString()} />
        <Stat label="columns" value={String(d.fields.length)} />
        <Stat label="version" value={`${d.version} of ${d.latest_version}`} />
        <Stat label="storage format" value={d.storage_version} />
      </div>

      <Eyebrow>Fields</Eyebrow>
      <div className="space-y-1.5 mb-7">
        {d.fields.map((f) => (
          <div
            key={f.name}
            className="flex items-baseline gap-3 mono text-[12px] px-2.5 py-1.5 rounded-sm"
            style={
              f.blob
                ? { background: "rgb(var(--video-rgb) / 0.12)", border: "1px solid rgb(var(--video-rgb) / 0.4)" }
                : { border: "1px solid transparent" }
            }
          >
            <span
              className="w-[180px] shrink-0"
              style={{ color: f.blob ? "var(--video)" : "var(--body)" }}
            >
              {f.name}
            </span>
            <span className="text-[var(--haze)] truncate">{f.type}</span>
            {!f.nullable && <span className="text-[10px] text-[var(--dim)]">NOT NULL</span>}
            {f.blob && (
              <span className="ml-auto text-[10px] shrink-0" style={{ color: "var(--video)" }}>
                BLOB — SIDE FILE
              </span>
            )}
          </div>
        ))}
      </div>

      <Eyebrow>On disk — {files.toLocaleString()} files</Eyebrow>
      {blob_bytes > 0 ? (
        <>
          <div className="flex h-9 rounded-sm overflow-hidden border border-[var(--rule)]">
            <div style={{ width: `${Math.max(metaPct, 0.4)}%`, background: "var(--index)" }} />
            <div className="flex-1 grid place-items-center"
                 style={{ background: "rgb(var(--video-rgb) / 0.22)" }}>
              <span className="mono text-[11px]" style={{ color: "var(--video)" }}>
                {fmtBytes(blob_bytes).value} {fmtBytes(blob_bytes).unit} in .blob side files
              </span>
            </div>
          </div>
          <p className="text-[12px] text-[var(--haze)] mt-3 leading-relaxed">
            <Bytes n={meta_bytes} tone="index" /> of everything a scan reads, against{" "}
            <Bytes n={blob_bytes} tone="video" /> a scan never opens — {ratio.toLocaleString()} to 1.
          </p>
        </>
      ) : (
        <p className="text-[12px] text-[var(--haze)] leading-relaxed">
          No blob columns. Everything this table holds is{" "}
          <Bytes n={meta_bytes} tone="index" /> of ordinary Lance files.
        </p>
      )}
    </>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="panel px-4 py-3">
      <div className="eyebrow mb-1">{label}</div>
      <div className="mono text-[16px] text-[var(--bright)]">{value}</div>
    </div>
  );
}

// ----------------------------------------------------------------- versions

function Delta({ n }: { n: number }) {
  if (n === 0) return <span className="text-[var(--dim)]">—</span>;
  return (
    <span style={{ color: n > 0 ? "var(--index)" : "var(--video)" }}>
      {n > 0 ? "+" : ""}{n.toLocaleString()}
    </span>
  );
}

export function VersionsTab({ d }: { d: Versions }) {
  const refs = Object.keys(d.tags).length + Object.keys(d.branches).length;
  return (
    <>
      <p className="text-[12px] text-[var(--haze)] mb-5 leading-relaxed">
        {d.versions.length} version{d.versions.length === 1 ? "" : "s"}, newest first.
        {refs === 0 && " No tags or branches."}
      </p>
      <table className="w-full">
        <thead>
          <tr>
            <Th>ver</Th><Th>operation</Th><Th>when</Th>
            <Th right>rows</Th><Th right>frags</Th><Th right>files</Th><Th right>manifest</Th>
          </tr>
        </thead>
        <tbody>
          {d.versions.map((v) => (
            <tr key={v.version} style={{ borderBottom: "1px solid var(--hairline)" }}>
              <Td>
                <span style={{ color: v.version === d.current_version ? "var(--video)" : undefined }}>
                  {v.version}
                </span>
              </Td>
              <Td dim={!v.operation}>{v.operation ?? "unknown"}</Td>
              <Td dim>{fmtWhen(v.timestamp)}</Td>
              <Td right>
                {v.rows.toLocaleString()}
                {v.diff && <span className="ml-2 text-[11px]"><Delta n={v.diff.rows} /></span>}
              </Td>
              <Td right>
                {v.fragments}
                {v.diff && <span className="ml-2 text-[11px]"><Delta n={v.diff.fragments} /></span>}
              </Td>
              <Td right>{v.data_files}</Td>
              <Td right>
                <Bytes n={v.manifest_bytes} />
                {v.diff && (
                  <span className="ml-2 text-[11px]"><Delta n={v.diff.manifest_bytes} /></span>
                )}
              </Td>
            </tr>
          ))}
        </tbody>
      </table>
      {d.versions.some((v) => v.diff && Object.values(v.diff).every((n) => n === 0)) && (
        <Caveat>
          A version whose row, fragment and byte counts are all unchanged did something
          that doesn&rsquo;t move them — building an index, for instance. The operation
          column is the only thing that tells you which.
        </Caveat>
      )}
    </>
  );
}

// ------------------------------------------------------------------ indices

export function IndicesTab({ d }: { d: Indices }) {
  const bare = d.unindexed_vector_columns;
  return (
    <>
      {d.indices.length === 0 ? (
        <Empty>No indices on this table. Every query is a scan.</Empty>
      ) : (
        <div className="space-y-3 mb-6">
          {d.indices.map((i) => (
            <div key={i.name} className="panel p-4">
              <div className="flex items-baseline justify-between mb-2">
                <span className="mono text-[13px] text-[var(--bright)]">{i.name}</span>
                <span className="eyebrow">{i.type}</span>
              </div>
              <div className="mono text-[12px] text-[var(--haze)] space-y-1">
                <div>on {i.columns.join(", ") || "—"}</div>
                <div>
                  covering {i.fragment_ids.length} fragment
                  {i.fragment_ids.length === 1 ? "" : "s"}
                  {i.indexed_rows !== null && (
                    <> · {i.indexed_rows.toLocaleString()} rows indexed</>
                  )}
                  {i.unindexed_rows ? (
                    <span style={{ color: "var(--video)" }}>
                      {" "}· {i.unindexed_rows.toLocaleString()} rows NOT indexed
                    </span>
                  ) : null}
                </div>
              </div>

            </div>
          ))}
        </div>
      )}

      <Eyebrow>Columns with no index</Eyebrow>
      <div className="flex flex-wrap gap-2 mb-4">
        {d.unindexed_columns.map((c) => {
          const notable = bare.includes(c.name);
          return (
            <span
              key={c.name}
              title={c.type}
              className="mono text-[11px] px-2.5 py-1.5 rounded-sm border"
              style={
                notable
                  ? { borderColor: "var(--video)", color: "var(--video)",
                      background: "rgb(var(--video-rgb) / 0.09)" }
                  : { borderColor: "var(--rule)",
                      color: c.indexable ? "var(--haze)" : "var(--dim)" }
              }
            >
              {c.name}
              {c.vector_dim ? <span className="ml-1.5">[{c.vector_dim}]</span> : null}
              {!c.indexable && <span className="ml-1.5 text-[10px]">blob</span>}
            </span>
          );
        })}
      </div>

      {/* The unindexed-vector caveat that used to live here is now a finding —
          same claim, computed rather than written, and rendered under this panel by
          `PanelFindings`. Two voices saying it was one voice too many. */}
    </>
  );
}

// ---------------------------------------------------------------- fragments

export function FragmentsTab({ d }: { d: Fragments }) {
  return (
    <>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <Stat label="fragments" value={String(d.stats.num_fragments)} />
        <Stat label="rows" value={d.rows.toLocaleString()} />
        <Stat label="deleted rows" value={d.stats.num_deleted_rows.toLocaleString()} />
        <Stat label="small files" value={String(d.stats.num_small_files)} />
      </div>

      <table className="w-full">
        <thead>
          <tr>
            <Th>id</Th><Th right>rows</Th><Th right>deleted</Th>
            <Th right>data</Th>
            {d.has_blob_columns && <><Th right>blob</Th><Th right>blobs</Th></>}
            <Th>file</Th>
          </tr>
        </thead>
        <tbody>
          {d.fragments.map((f) => (
            <tr key={f.id} style={{ borderBottom: "1px solid var(--hairline)" }}>
              <Td>{f.id}</Td>
              <Td right>{f.rows.toLocaleString()}</Td>
              <Td right dim={f.deleted_rows === 0}>{f.deleted_rows}</Td>
              <Td right><Bytes n={f.data_bytes} tone="index" /></Td>
              {d.has_blob_columns && (
                <>
                  <Td right><Bytes n={f.blob_bytes} tone="video" /></Td>
                  <Td right dim>{f.blob_files}</Td>
                </>
              )}
              <Td dim className="truncate max-w-[180px]">
                {f.data_files[0]?.path.slice(0, 12)}… v{f.data_files[0]?.file_version}
              </Td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* The small-file caveat is a finding now — the rule that reports the count
          is the same rule that says why acting on it would be wrong. */}
    </>
  );
}

// --------------------------------------------------------------------- rows

export function CellView({ v }: { v: Cell }) {
  if (v === null) return <span className="text-[var(--dim)]">null</span>;
  if (typeof v === "object") {
    if ("blob" in v) {
      const b = fmtBytes(v.size_bytes ?? 0);
      return (
        <span style={{ color: "var(--video)" }} title="described from its Blob V2 descriptor — not read">
          blob {b.value} {b.unit}
        </span>
      );
    }
    if ("vector_dim" in v) {
      return (
        <span className="text-[var(--haze)]" title={v.head.join(", ")}>
          [{v.head.slice(0, 3).map((n) => n.toFixed(3)).join(", ")}, …] ×{v.vector_dim}
        </span>
      );
    }
    const b = fmtBytes(v.bytes);
    return <span style={{ color: "var(--index)" }}>{b.value} {b.unit}</span>;
  }
  if (typeof v === "number") return <>{Number.isInteger(v) ? v.toLocaleString() : v.toFixed(3)}</>;
  return <>{String(v)}</>;
}

/** Ask in English, get a predicate to read before you run it.
 *
 *  Three things make this a draft rather than an answer. It lands in the filter box
 *  instead of being applied. It is dry-run counted, so "matches 99 of 1,114" tells
 *  you whether it understood the question before you spend a page read on it. And a
 *  refusal is a first-class outcome — a model that says it cannot express something
 *  is more useful than one that produces a filter that runs and means something
 *  else. */
function AskForFilter({ table, model, onDraft }: {
  table: string;
  model: string;
  onDraft: (filter: string) => void;
}) {
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<FilterDraft | null>(null);

  const ask = async () => {
    if (!question.trim()) return;
    setBusy(true);
    setResult(null);
    try {
      const r = await askForFilter(table, question);
      setResult(r);
      if (r.filter) onDraft(r.filter);
    } catch (e) {
      setResult({ ok: false, error: e instanceof Error ? e.message : "ask failed" });
    } finally {
      setBusy(false);
    }
  };

  const tone = result?.valid ? "index" : "video";

  return (
    <div className="mb-4">
      <div className="flex gap-2">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); ask(); } }}
          placeholder="Ask in English — moments in the Go devroom, more than ten minutes in"
          className="flex-1 bg-[var(--ink-3)] border border-[var(--rule)] rounded-sm
                     px-3 py-2 text-[12px] text-[var(--bright)] outline-none
                     focus:border-[var(--index)] transition-colors
                     placeholder:text-[var(--dim)]"
        />
        <button type="button" onClick={ask} disabled={busy || !question.trim()}
                className="btn mono text-[10px] tracking-[0.14em] uppercase">
          <Icon name="spark" size={14} />
          {busy ? "asking…" : "Translate"}
        </button>
      </div>

      {busy && (
        <p className="mono text-[10px] text-[var(--haze)] mt-2">
          {model} is writing a predicate. It lands in the filter box below for you to
          read — nothing runs until you say so.
        </p>
      )}

      {result && (
        <div className="mt-2 px-3.5 py-3 rounded-sm border"
             style={{ borderColor: `rgb(var(--${tone}-rgb) / 0.4)`,
                      background: `rgb(var(--${tone}-rgb) / 0.06)` }}>
          {result.valid ? (
            <div className="mono text-[12px]" style={{ color: "var(--index)" }}>
              matches {result.matched_rows?.toLocaleString()} of{" "}
              {result.total_rows?.toLocaleString()} rows
            </div>
          ) : (
            <div className="mono flex items-center gap-2 text-[12px]"
                 style={{ color: "var(--video)" }}>
              <Icon name="warning" size={14} />
              {result.confidence === "refuse"
                ? "this cannot be asked of these columns"
                : (result.error ?? "no filter produced")}
            </div>
          )}

          {result.explanation && (
            <p className="text-[12px] text-[var(--body)] leading-relaxed mt-1.5">
              {result.explanation}
            </p>
          )}
          {result.setup_hint && (
            <p className="text-[12px] text-[var(--haze)] leading-relaxed mt-1.5">
              {result.setup_hint}
            </p>
          )}

          {/* Both costs, because both were spent: bytes off the disk to describe the
              table, and tokens to write the sentence. */}
          <div className="mono text-[10px] text-[var(--haze)] mt-2">
            {result.model} · {((result.ms ?? 0) / 1000).toFixed(1)}s ·{" "}
            {result.cost_usd === 0
              ? "no cost, ran locally"
              : result.cost_usd == null ? "cost unknown" : `$${result.cost_usd.toFixed(5)}`}
            {result.context_read_bytes != null && (
              <> · {fmtBytes(result.context_read_bytes).value}{" "}
                {fmtBytes(result.context_read_bytes).unit} read to describe the table</>
            )}
            {result.values_included && result.faceted_columns?.length ? (
              <> · sent values of {result.faceted_columns.join(", ")}</>
            ) : result.values_included === false ? (
              <> · schema only, no row values sent</>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}

export function RowsTab({
  d, onPage, onFilter, onExpand, expanded, error, table, ai,
}: {
  d: Rows | null;
  onPage: (offset: number) => void;
  onFilter: (f: string) => void;
  onExpand: (col: string) => void;
  expanded: string[];
  error: string | null;
  table: string | null;
  ai: Capabilities | null;
}) {
  const [draft, setDraft] = useState(d?.filter ?? "");

  return (
    <>
      {ai?.available && table && (
        <AskForFilter
          table={table}
          model={ai.models_by_role.fast.id}
          onDraft={setDraft}
        />
      )}

      <form
        onSubmit={(e) => { e.preventDefault(); onFilter(draft); }}
        className="flex gap-2 mb-4"
      >
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="SQL predicate — track = 'Go' and year = 2025"
          className="flex-1 bg-[var(--ink-3)] border border-[var(--rule)] rounded-sm
                     px-3 py-2 mono text-[12px] text-[var(--bright)] outline-none
                     focus:border-[var(--video)] transition-colors placeholder:text-[var(--dim)]"
        />
        <button type="submit" className="btn mono text-[10px] tracking-[0.14em] uppercase">
          <Icon name="search" size={14} />
          Filter
        </button>
      </form>

      {error && (
        <div
          className="mono flex items-center gap-2.5 text-[12px] px-3.5 py-3 rounded-sm mb-4"
          style={{ background: "rgb(var(--video-rgb) / 0.12)", border: "1px solid rgb(var(--video-rgb) / 0.4)",
                   color: "var(--video)" }}
        >
          <Icon name="warning" size={15} />
          {error}
        </div>
      )}

      {!d ? null : (
        <>
          {d.omitted_columns.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 mb-4">
              <span className="eyebrow">Not read</span>
              {d.omitted_columns.map((c) => (
                <button
                  key={c.name}
                  onClick={() => onExpand(c.name)}
                  title={`${c.type} — click to read it and see what it costs`}
                  className="btn mono !h-[26px] !px-2.5 text-[11px]"
                >
                  <Icon name="plus" size={12} />
                  {c.name}{c.vector_dim ? `[${c.vector_dim}]` : ""}
                </button>
              ))}
              {expanded.map((c) => (
                <button
                  key={c}
                  onClick={() => onExpand(c)}
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

          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr>{d.columns.map((c) => <Th key={c}>{c}</Th>)}</tr>
              </thead>
              <tbody>
                {d.rows.map((r, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid var(--hairline)" }}>
                    {d.columns.map((c) => (
                      <Td key={c} className="max-w-[280px] truncate">
                        <CellView v={r[c]} />
                      </Td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {d.returned === 0 && <Empty>No rows here.</Empty>}

          <div className="flex items-center justify-between mt-5">
            <span className="mono text-[11px] text-[var(--haze)]">
              {d.total_rows === 0
                ? "0 rows"
                : `${d.offset + 1}–${d.offset + d.returned} of ${d.total_rows.toLocaleString()}`}
            </span>
            <div className="flex gap-2">
              <PageBtn
                disabled={d.offset === 0}
                onClick={() => onPage(Math.max(0, d.offset - d.limit))}
              >
                <Icon name="chevronLeft" size={13} />
                prev
              </PageBtn>
              <PageBtn
                disabled={d.offset + d.returned >= d.total_rows}
                onClick={() => onPage(d.offset + d.limit)}
              >
                next
                <Icon name="chevronRight" size={13} />
              </PageBtn>
            </div>
          </div>
        </>
      )}
    </>
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

export { Cost };
