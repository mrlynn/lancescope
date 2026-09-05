"use client";

/** Is this table ready to train on?
 *
 *  Every other tab answers a question about the table. This one answers a question
 *  about a job that has not run yet, which is a different shape: what a loader will
 *  do with this split, what one pass costs, and what an eval query costs on top of
 *  it. The numbers were all here already — spread across Schema, Fragments and
 *  Indices, each sitting beside the panel that produced it and none of them beside
 *  each other.
 *
 *  Two kinds of thing on this screen, deliberately not mixed. The strip at the top
 *  is measurement: it is true of every table and it is stated whether or not it is
 *  interesting, because "what does an epoch read" is a question you asked rather
 *  than an alert somebody raised. Below it are findings, which are conditional by
 *  construction — a rule fired, or it did not, and an empty list here is good news
 *  rather than a missing panel.
 *
 *  What it does not claim is on the screen too. This reads the layout. Whether the
 *  labels are right, whether a split leaks, whether the corpus is worth training on
 *  at all — none of that is visible from a manifest, and a panel headed "training"
 *  that stayed quiet about the difference would be read as a clean bill of health it
 *  has no way to give.
 */

import { useEffect, useMemo, useState } from "react";

import Icon from "@/app/components/Icon";
import { Copy, Empty, Eyebrow } from "@/app/components/console/atoms";
import { FindingCard, PartialAnalysis } from "@/app/components/console/Findings";
import { fmtBytes } from "@/app/lib/api";
import { getEstimate, getRunConfig } from "@/app/lib/catalog";
import type { ColumnCost, Estimate, Findings, TableDetail } from "@/app/lib/catalog";

/** Findings a training run is the one paying for. The facet is computed by the same
 *  rules that computed the finding, so this is a filter rather than a judgement. */
export const trainingFindings = (d: Findings | null) =>
  (d?.findings ?? []).filter((f) => f.facets.includes("training"));

export function TrainingTab({ d, findings }: {
  d: TableDetail | null;
  findings: Findings | null;
}) {
  // Split so the loading case can return before any hook exists. The body below
  // fetches, and a component that returns early above its own hooks is the one React
  // error this file would otherwise be guaranteed to hit.
  if (!d) return <Empty>reading the table…</Empty>;
  return <Ready d={d} findings={findings} />;
}

function Ready({ d, findings }: { d: TableDetail; findings: Findings | null }) {
  const mine = trainingFindings(findings);
  const warn = mine.filter((f) => f.severity === "warn").length;
  const note = mine.length - warn;

  // Unknown is not the same as zero. Where the split could not be walked — a bucket,
  // a namespace — `on_disk` is null, and the tile `heavy` gates shows a byte total
  // built from these two numbers. Treating null as "no blob bytes" would print that
  // total as a measurement. The table may well still be heavy; `d.blob_columns` says
  // so, and the panel simply declines to put a figure on it.
  const { meta_bytes, blob_bytes } = d.on_disk ?? { meta_bytes: 0, blob_bytes: 0 };
  const heavy = d.on_disk !== null && blob_bytes > 0;
  const frags = d.stats.num_fragments;

  // What the columns weigh. Fetched here rather than with the tab because it is the
  // only read on this screen that opens a data file; everything else is manifests.
  // Held with the table it describes, rather than cleared in an effect when the
  // table changes. Resetting state synchronously inside an effect is a cascading
  // render; comparing on the way out means a stale answer simply is not shown.
  const at = `${d.name}@${d.version}`;
  const [loaded, setLoaded] = useState<{ at: string; est: Estimate } | null>(null);
  const [drops, setDrops] = useState<{ at: string; names: string[] }>({ at, names: [] });
  const est = loaded?.at === at ? loaded.est : null;
  // Memoised so the fallback is not a fresh array on every render, which would make
  // the projection below recompute continuously.
  const dropped = useMemo(
    () => (drops.at === at ? drops.names : []),
    [drops, at],
  );

  useEffect(() => {
    let live = true;
    getEstimate(d.name)
      .then((e) => { if (live) setLoaded({ at: `${d.name}@${d.version}`, est: e }); })
      .catch(() => {});
    return () => { live = false; };
  }, [d.name, d.version]);

  // Deselecting recomputes from figures already in hand. A round trip per checkbox
  // would make the one interaction this panel exists for feel like a page load.
  const kept = useMemo(
    () => (est?.columns ?? []).filter((c) => !dropped.includes(c.name)),
    [est, dropped],
  );
  const weighed = kept.reduce((n, c) => n + c.bytes, 0);
  // The floor scales with what is still selected: it is per data file, and dropping
  // columns cannot take a pass below what opening those files costs.
  const shown = est
    ? Math.max(weighed, est.bytes ? Math.round(est.floor_bytes * (weighed / est.bytes)) : 0)
    : 0;
  // Every floor exceeds its weight — a pass always pays footers. What is worth saying
  // out loud is the case where the overhead *dominates*, which is what a table of
  // small files looks like: there, dropping columns buys nothing at all. Half again
  // is the line; on this repository's `moments` the floor is 0.04% above the weight
  // and on `segments` it is six times it.
  const floorRules = !!est && shown > weighed * 1.5;

  return (
    <>
      <Verdict warn={warn} note={note} loaded={findings !== null} />

      {/* ------------------------------------------------------------ measured */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
        {/* Was `on_disk.meta_bytes`, which is the directory walk's total for every
            file under the table — indices, manifests, old versions and all. True,
            and not what a pass reads. This is what the projected columns weigh, from
            the file footers: narrower, and available on a remote root where there is
            no directory to walk. */}
        <Tile label="one epoch reads"
              value={est
                ? <Bytes n={shown} />
                : <span className="text-[15px] text-[var(--haze)]">weighing…</span>}
              foot={!est
                ? <>reading the footers — no rows are opened</>
                : floorRules
                  ? <>the floor: these data files are small enough that Lance reads
                      each one whole, so dropping columns would not cost less</>
                  : <>{kept.length} of {est.columns.length} column
                      {est.columns.length === 1 ? "" : "s"}, weighed from the
                      footers</>} />

        {/* The second number only exists on a table that has a large half, and on
            one that does it is the number a GPU budget is actually made of.

            No ratio in the foot. The blob-split finding renders inches below this
            with 37,978:1 on it — blob against metadata — and the ratio of the two
            figures in this strip is metadata-plus-blob against metadata, which is
            37,979:1. Both true, one apart, and side by side they read as one number
            that cannot make its mind up. The ratio is said once, where it was
            computed. */}
        {heavy && (
          <Tile label="…or with the media"
                value={<Bytes n={meta_bytes + blob_bytes} tone="video" />}
                foot={<>the same pass, plus every side file it skipped — once per
                  epoch</>} />
        )}

        <Tile label="loader ceiling"
              value={<span style={{ color: frags === 1 ? "var(--video)" : undefined }}>
                {frags.toLocaleString()}<span className="text-[13px] text-[var(--haze)]">
                  {" "}worker{frags === 1 ? "" : "s"}</span>
              </span>}
              foot={<>one fragment per worker — past {frags.toLocaleString()} the rest
                are handed nothing</>} />

        <Tile label="rows" value={d.rows.toLocaleString()}
              foot={<>at version {d.version} of {d.latest_version}
                {d.version === d.latest_version ? " — the newest" : " — not the newest"}</>} />

        {!heavy && (
          <Tile label="version to pin" value={`v${d.version}`}
                foot={<>record it in the run config; Compare shows what a later write
                  changed</>} />
        )}
      </div>

      {/* --------------------------------------------------------- the columns */}
      {est && est.columns.length > 1 && (
        <ColumnWeights est={est} dropped={dropped} onToggle={(name) =>
          setDrops(() => ({
            at,
            names: dropped.includes(name)
              ? dropped.filter((n) => n !== name)
              : [...dropped, name],
          }))} />
      )}

      {est && est.caveats.length > 0 && (
        <div className="mt-4 space-y-2">
          {est.caveats.map((c) => (
            <p key={c} className="text-[11px] text-[var(--haze)] leading-relaxed
                                  pl-3 max-w-[76ch]"
               style={{ borderLeft: "1px solid var(--rule)" }}>{c}</p>
          ))}
        </div>
      )}

      {/* ------------------------------------------------------------ findings */}
      <PartialAnalysis d={findings} />

      {findings === null ? (
        <Empty>working out what this table has to say…</Empty>
      ) : mine.length === 0 ? (
        <Empty>
          Nothing in this table&rsquo;s layout will slow a run down. Every rule that
          bears on a training job was checked against{" "}
          <span className="mono text-[var(--bright)]">{d.name}</span> and none of them
          fired.
        </Empty>
      ) : (
        <>
          <Eyebrow>What a run would pay for</Eyebrow>
          <div className="space-y-3 mt-3">
            {mine.map((f) => <FindingCard key={f.id} f={f} />)}
          </div>
        </>
      )}

      <RunConfigBlock table={d.name}
                      columns={dropped.length ? kept.map((c) => c.name) : undefined} />

      <Limit />
    </>
  );
}

/** The headline, and only ever about the layout.
 *
 *  "Ready to train" is a sentence about the data, and nothing here has read any. So
 *  the verdict is scoped to what was actually measured, every time, including when
 *  it is good news. */
function Verdict({ warn, note, loaded }: {
  warn: number; note: number; loaded: boolean;
}) {
  if (!loaded) return null;
  const tone = warn > 0 ? "video" : note > 0 ? "index" : null;
  const said = warn > 0
    ? `${warn} thing${warn === 1 ? "" : "s"} in this layout will cost a run time`
    : note > 0
      ? `Nothing here will stall a loader. ${note} worth knowing about first`
      : "Nothing in this layout will slow a run down";

  return (
    <div className="flex items-start gap-3 px-4 py-3.5 rounded-sm mb-6"
         style={tone
           ? { background: `rgb(var(--${tone}-rgb) / 0.08)`,
               border: `1px solid rgb(var(--${tone}-rgb) / 0.35)` }
           : { border: "1px solid var(--rule)" }}>
      <span className="pt-0.5 shrink-0"
            style={{ color: tone ? `var(--${tone})` : "var(--haze)" }}>
        <Icon name={warn > 0 ? "warning" : "check"} size={16} />
      </span>
      <div className="min-w-0">
        <div className="text-[14px]" style={{ color: tone ? `var(--${tone})` : "var(--bright)" }}>
          {said}.
        </div>
        <div className="text-[12px] text-[var(--haze)] mt-1 leading-relaxed">
          Derived from the same manifests the other tabs read — no model, no tokens.
        </div>
      </div>
    </div>
  );
}

/** Which column is the epoch.
 *
 *  The single most useful thing the per-column weights make sayable, and it is
 *  usually one row: a thumbnail column is 81% of what a pass over the demo corpus
 *  moves, and a loader that does not need it reads a fifth of the table. The bars
 *  are there so that is visible before the numbers are read.
 *
 *  Clicking a column out recomputes from figures already fetched. Blob columns are
 *  absent rather than disabled — a scan never reads them, so offering them here
 *  would imply a choice that does not exist. */
function ColumnWeights({ est, dropped, onToggle }: {
  est: Estimate; dropped: string[]; onToggle: (name: string) => void;
}) {
  const widest = est.columns[0]?.bytes || 1;
  return (
    <div className="mt-6">
      <Eyebrow>What each column weighs</Eyebrow>
      <div className="mt-3 space-y-1">
        {est.columns.map((c) => (
          <ColumnRow key={c.name} c={c} widest={widest}
                     off={dropped.includes(c.name)} onToggle={onToggle} />
        ))}
      </div>
      <p className="text-[11px] text-[var(--haze)] mt-3 leading-relaxed max-w-[70ch]">
        Click a column to take it out of the projection. The figure above follows —
        no further reads, because the weights are already here.
      </p>
    </div>
  );
}

function ColumnRow({ c, widest, off, onToggle }: {
  c: ColumnCost; widest: number; off: boolean; onToggle: (name: string) => void;
}) {
  const { value, unit } = fmtBytes(c.bytes);
  return (
    <button onClick={() => onToggle(c.name)}
            className="w-full flex items-center gap-3 px-2 py-1 rounded-sm text-left
                       hover:bg-[var(--ink-3)]"
            aria-pressed={!off}>
      <span className={`mono text-[12px] w-40 truncate ${off
        ? "text-[var(--dim)] line-through" : "text-[var(--bright)]"}`}>{c.name}</span>
      <span className="flex-1 h-[6px] rounded-sm overflow-hidden"
            style={{ background: "var(--ink-3)" }}>
        <span className="block h-full rounded-sm"
              style={{ width: `${Math.max(1, (c.bytes / widest) * 100)}%`,
                       background: off ? "var(--dim)" : "var(--index)" }} />
      </span>
      <span className={`mono text-[11px] w-24 text-right ${off
        ? "text-[var(--dim)]" : "text-[var(--haze)]"}`}>{value} {unit}</span>
    </button>
  );
}

/** The artifact. Server-rendered YAML, shown verbatim.
 *
 *  The tab has been telling people to record this in a run config since it was
 *  written. The string comes from the server for the same reason the query tab's
 *  reproduction does: a console that assembled its own would eventually disagree
 *  with the one `lancescope run-config` writes, and the disagreement would surface
 *  as a training run pinned to the wrong version. */
function RunConfigBlock({ table, columns }: { table: string; columns?: string[] }) {
  const [open, setOpen] = useState(false);
  const key = `${table}|${columns?.join(",") ?? ""}`;
  // Same discipline as the estimate above: the answer carries the request it answers,
  // so a projection changed while the block is open never shows the previous YAML
  // under the new heading.
  const [got, setGot] = useState<{ key: string; yaml: string } | null>(null);
  const yaml = got?.key === key ? got.yaml : null;

  useEffect(() => {
    if (!open || yaml !== null) return;
    let live = true;
    getRunConfig(table, columns)
      .then((r) => { if (live) setGot({ key, yaml: r.run_config_yaml }); })
      .catch(() => { if (live) setGot({ key, yaml: "# could not read this table\n" }); });
    return () => { live = false; };
  }, [open, yaml, table, key, columns]);

  return (
    <div className="mt-6 pt-5" style={{ borderTop: "1px solid var(--rule)" }}>
      <div className="flex items-center gap-2">
        <button onClick={() => setOpen((v) => !v)}
                className="text-[12px] text-[var(--haze)] hover:text-[var(--bright)]
                           flex items-center gap-1.5">
          <Icon name={open ? "close" : "plus"} size={11} />
          run config
        </button>
        {open && yaml && <Copy value={yaml} what="run config" size={13} />}
      </div>
      {open && (
        yaml === null
          ? <Empty>assembling it…</Empty>
          : <pre className="mono text-[10px] mt-3 p-3 rounded-sm overflow-x-auto
                            whitespace-pre text-[var(--haze)]"
                 style={{ background: "var(--ink-3)", border: "1px solid var(--rule)" }}>
              {yaml}
            </pre>
      )}
      <p className="text-[11px] text-[var(--haze)] mt-2 leading-relaxed max-w-[70ch]">
        What a run must pin: the version, the columns, what they weigh, and how many
        workers this split can feed. The same block{" "}
        <span className="mono text-[var(--bright)]">lancescope run-config {table}</span>{" "}
        writes.
      </p>
    </div>
  );
}

function Limit() {
  return (
    <p className="text-[12px] text-[var(--haze)] leading-relaxed mt-6 pt-5 max-w-[68ch]"
       style={{ borderTop: "1px solid var(--rule)" }}>
      All of this is the <span className="text-[var(--bright)]">layout</span>: how the
      table is split, what a pass moves, what a query costs on top of it. Whether the
      labels are right, whether a train/eval split leaks, whether this corpus is worth
      training on at all — none of that is in a manifest, so none of it is here. A
      quiet panel means nothing will stall a loader, not that the dataset is good.
    </p>
  );
}

function Bytes({ n, tone }: { n: number; tone?: "video" }) {
  const { value, unit } = fmtBytes(n);
  return (
    <span style={tone ? { color: `var(--${tone})` } : undefined}>
      {value}<span className="text-[13px] text-[var(--haze)]"> {unit}</span>
    </span>
  );
}

function Tile({ label, value, foot }: {
  label: string; value: React.ReactNode; foot: React.ReactNode;
}) {
  return (
    <div className="panel px-4 py-3 flex flex-col">
      <div className="eyebrow mb-1">{label}</div>
      <div className="mono text-[19px] text-[var(--bright)] leading-tight">{value}</div>
      <div className="text-[11px] text-[var(--haze)] leading-relaxed mt-1.5">{foot}</div>
    </div>
  );
}
