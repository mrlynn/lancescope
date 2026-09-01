"use client";

/** Two versions of a table, side by side.
 *
 *  The versions panel says what happened. This says whether it helped. Both sides
 *  are pinned to explicit version numbers, so a dataset written to while this is on
 *  screen cannot produce a before from one moment and an after from another — and
 *  the same query runs against both, which turns "the index exists now" into a byte
 *  count and an access path that either changed or did not. */

import { useCallback, useEffect, useState } from "react";
import Icon from "@/app/components/Icon";
import { Caveat, Empty, Eyebrow, fmtWhen } from "@/app/components/console/atoms";
import { fmtBytes } from "@/app/lib/api";
import {
  ApiError,
  type Comparison, type QueryComparison, type QuerySpec, type Versions,
  compareQuery, compareVersions, getVersions,
} from "@/app/lib/catalog";

export function CompareTab({ table }: { table: string }) {
  const [versions, setVersions] = useState<Versions | null>(null);
  const [a, setA] = useState<number | null>(null);
  const [b, setB] = useState<number | null>(null);
  const [cmp, setCmp] = useState<Comparison | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [mode, setMode] = useState<QuerySpec["mode"]>("scan");
  const [filter, setFilter] = useState("");
  const [text, setText] = useState("");
  const [qcmp, setQcmp] = useState<QueryComparison | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getVersions(table)
      .then((v) => {
        setVersions(v);
        const all = v.versions.map((x) => x.version).sort((x, y) => x - y);
        // Oldest against newest: the widest available before/after, and the one
        // someone opening this tab almost always means.
        setA(all[0] ?? null);
        setB(all[all.length - 1] ?? null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "no version history"));
  }, [table]);

  useEffect(() => {
    if (a === null || b === null) return;
    setQcmp(null);
    compareVersions(table, a, b)
      .then((c) => { setCmp(c); setError(null); })
      .catch((e) => { setCmp(null); setError(e instanceof ApiError ? e.message : "compare failed"); });
  }, [table, a, b]);

  const runQuery = useCallback(async () => {
    if (a === null || b === null) return;
    setBusy(true);
    try {
      const spec: QuerySpec = {
        mode,
        filter: filter.trim() || null,
        limit: 25,
        ...(mode === "fts" ? { text } : {}),
      };
      setQcmp(await compareQuery(table, a, b, spec));
      setError(null);
    } catch (e) {
      setQcmp(null);
      setError(e instanceof ApiError ? e.message : "the query could not be run");
    } finally {
      setBusy(false);
    }
  }, [table, a, b, mode, filter, text]);

  if (!versions) return <Empty>{error ?? "reading version history…"}</Empty>;
  if (versions.versions.length < 2) {
    return <Empty>This table has one version. There is nothing to compare it with.</Empty>;
  }

  return (
    <>
      <div className="flex flex-wrap items-end gap-3 mb-5">
        <VersionPicker label="before" value={a} onChange={setA} d={versions} />
        <Icon name="arrowRight" size={16} />
        <VersionPicker label="after" value={b} onChange={setB} d={versions} />
      </div>

      {error && (
        <div className="mono flex items-center gap-2.5 text-[12px] px-3.5 py-3 rounded-sm mb-4"
             style={{ background: "rgb(var(--video-rgb) / 0.12)",
                      border: "1px solid rgb(var(--video-rgb) / 0.4)", color: "var(--video)" }}>
          <Icon name="warning" size={15} />
          {error}
        </div>
      )}

      {cmp && <Structural c={cmp} />}

      <div className="mt-7">
        <Eyebrow>The same query, both sides</Eyebrow>
        <p className="text-[12px] text-[var(--haze)] leading-relaxed mt-2 mb-3 max-w-[70ch]">
          This is what makes an operation legible. A query that reads less after an
          index build, or takes a different path, is the before and after — and one
          that could not run at all on the earlier version says more than either.
        </p>

        <div className="flex flex-wrap items-end gap-2">
          <div className="seg">
            {(["scan", "fts"] as const).map((m) => (
              <button key={m} onClick={() => setMode(m)} data-on={mode === m}
                      className="mono !px-3.5 text-[10px] tracking-[0.14em] uppercase">
                {m === "scan" ? "filter" : "full text"}
              </button>
            ))}
          </div>
          {mode === "fts" && (
            <input className="qin flex-1 min-w-[200px]" value={text}
                   onChange={(e) => setText(e.target.value)}
                   onKeyDown={(e) => e.key === "Enter" && runQuery()}
                   placeholder="kubernetes" />
          )}
          <input className="qin mono flex-1 min-w-[200px]" value={filter}
                 onChange={(e) => setFilter(e.target.value)}
                 onKeyDown={(e) => e.key === "Enter" && runQuery()}
                 placeholder="track = 'Go'" />
          <button className="btn btn-accent mono text-[10px] tracking-[0.14em] uppercase"
                  onClick={runQuery} disabled={busy}>
            <Icon name="search" size={14} />
            {busy ? "running…" : "Run on both"}
          </button>
        </div>

        {qcmp && <QueryDiff q={qcmp} />}
      </div>
    </>
  );
}

function VersionPicker({ label, value, onChange, d }: {
  label: string; value: number | null; onChange: (v: number) => void; d: Versions;
}) {
  return (
    <label className="block">
      <span className="eyebrow block mb-1.5">{label}</span>
      <select className="qin w-auto" value={value ?? ""}
              onChange={(e) => onChange(Number(e.target.value))}>
        {[...d.versions].sort((x, y) => x.version - y.version).map((v) => (
          <option key={v.version} value={v.version}>
            v{v.version}{v.operation ? ` · ${v.operation}` : ""}
          </option>
        ))}
      </select>
    </label>
  );
}

function Structural({ c }: { c: Comparison }) {
  const { diff } = c;
  const idxAdded = diff.indices.added;
  const idxRemoved = diff.indices.removed;

  return (
    <div className="rounded-sm border p-4"
         style={{ borderColor: "var(--rule)", background: "var(--ink-3)" }}>
      <div className="grid grid-cols-2 gap-6">
        <SideCard label="before" s={c.a} />
        <SideCard label="after" s={c.b} />
      </div>

      <div className="mt-4 pt-4 space-y-1.5" style={{ borderTop: "1px solid var(--hairline)" }}>
        {diff.unchanged && idxAdded.length === 0 && idxRemoved.length === 0 ? (
          <p className="text-[12px] text-[var(--body)] leading-relaxed">
            Nothing structural changed between these two. Whatever this version did,
            it did not move rows, fragments, columns or indices — the operation
            column on the versions tab is the only thing that will tell you what.
          </p>
        ) : (
          <>
            <Delta label="rows" n={diff.rows} />
            <Delta label="fragments" n={diff.fragments} />
            <Delta label="small files" n={diff.small_files} />
            <Delta label="deleted rows" n={diff.deleted_rows} />
            {idxAdded.map((n) => (
              <p key={n} className="text-[12px]" style={{ color: "var(--index)" }}>
                index built: <span className="mono">{n}</span>
              </p>
            ))}
            {idxRemoved.map((n) => (
              <p key={n} className="text-[12px]" style={{ color: "var(--video)" }}>
                index dropped: <span className="mono">{n}</span>
              </p>
            ))}
            {diff.schema.added.map((n) => (
              <p key={n} className="text-[12px] text-[var(--body)]">
                column added: <span className="mono text-[var(--bright)]">{n}</span>
              </p>
            ))}
            {diff.schema.removed.map((n) => (
              <p key={n} className="text-[12px] text-[var(--body)]">
                column removed: <span className="mono text-[var(--bright)]">{n}</span>
              </p>
            ))}
            {Object.entries(diff.schema.retyped).map(([n, t]) => (
              <p key={n} className="text-[12px] text-[var(--body)]">
                <span className="mono text-[var(--bright)]">{n}</span> retyped{" "}
                <span className="mono">{t.from}</span> → <span className="mono">{t.to}</span>
              </p>
            ))}
          </>
        )}
      </div>

      <Caveat>{diff.on_disk_note}</Caveat>
    </div>
  );
}

function SideCard({ label, s }: { label: string; s: Comparison["a"] }) {
  return (
    <div>
      <Eyebrow>{label} · v{s.version}</Eyebrow>
      <div className="mono text-[12px] text-[var(--body)] mt-1.5 space-y-0.5">
        <div>{s.rows.toLocaleString()} rows · {s.fragments} fragment{s.fragments === 1 ? "" : "s"}</div>
        <div className="text-[var(--haze)]">{fmtWhen(s.timestamp)}</div>
        {s.operation && <div className="text-[var(--haze)]">{s.operation}</div>}
        <div className="text-[var(--haze)]">
          {Object.keys(s.indices).length
            ? `indices: ${Object.keys(s.indices).join(", ")}`
            : "no indices"}
        </div>
      </div>
    </div>
  );
}

function Delta({ label, n }: { label: string; n: number }) {
  if (n === 0) return null;
  return (
    <p className="text-[12px] text-[var(--body)]">
      {label}{" "}
      <span className="mono" style={{ color: n > 0 ? "var(--index)" : "var(--video)" }}>
        {n > 0 ? "+" : ""}{n.toLocaleString()}
      </span>
    </p>
  );
}

function QueryDiff({ q }: { q: QueryComparison }) {
  return (
    <div className="rounded-sm border p-4 mt-3"
         style={{ borderColor: "var(--rule)", background: "var(--ink-3)" }}>
      <div className="grid grid-cols-2 gap-6">
        <QuerySide label={`v${q.versions.a}`} r={q.a} error={q.a_error} />
        <QuerySide label={`v${q.versions.b}`} r={q.b} error={q.b_error} />
      </div>

      <div className="mt-4 pt-4" style={{ borderTop: "1px solid var(--hairline)" }}>
        {q.ran_both ? (
          <p className="text-[13px] text-[var(--body)] leading-relaxed">
            {q.bytes_delta === 0 ? (
              "The same bytes either way."
            ) : (
              <>
                Reads{" "}
                <span className="mono"
                      style={{ color: (q.bytes_delta ?? 0) < 0 ? "var(--index)" : "var(--video)" }}>
                  {fmtBytes(Math.abs(q.bytes_delta ?? 0)).value}{" "}
                  {fmtBytes(Math.abs(q.bytes_delta ?? 0)).unit}
                </span>{" "}
                {(q.bytes_delta ?? 0) < 0 ? "less" : "more"} after
                {q.bytes_ratio && q.bytes_ratio !== 1
                  ? ` — ${q.bytes_ratio}× the bytes before.`
                  : "."}
              </>
            )}
            {q.paths_changed
              ? " The access path changed, which is where the difference comes from."
              : " The access path is the same on both sides."}
          </p>
        ) : (
          <p className="text-[13px] leading-relaxed" style={{ color: "var(--index)" }}>
            {q.verdict}
          </p>
        )}
      </div>
    </div>
  );
}

function QuerySide({ label, r, error }: {
  label: string; r: QueryComparison["a"]; error: string | null;
}) {
  if (!r) {
    return (
      <div>
        <Eyebrow>{label}</Eyebrow>
        <div className="mono text-[12px] mt-1.5 leading-relaxed" style={{ color: "var(--video)" }}>
          could not run
        </div>
        <div className="mono text-[10px] text-[var(--haze)] mt-1 leading-relaxed">
          {error}
        </div>
      </div>
    );
  }
  const b = fmtBytes(r.read_bytes);
  return (
    <div>
      <Eyebrow>{label}</Eyebrow>
      <div className="mono text-[15px] text-[var(--bright)] mt-1.5">
        {b.value} <span className="text-[0.7em] text-[var(--haze)]">{b.unit}</span>
      </div>
      <div className="mono text-[10px] text-[var(--haze)] mt-1">
        {r.returned} rows · {r.ms} ms · {r.read_iops} ios
      </div>
      <div className="mono text-[11px] mt-1" style={{ color: "var(--index)" }}>
        {r.plan.paths.map((p) => p.name).join(", ") || "plain read"}
      </div>
    </div>
  );
}
