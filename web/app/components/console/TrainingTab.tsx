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

import Icon from "@/app/components/Icon";
import { Empty, Eyebrow } from "@/app/components/console/atoms";
import { FindingCard, PartialAnalysis } from "@/app/components/console/Findings";
import { fmtBytes } from "@/app/lib/api";
import type { Findings, TableDetail } from "@/app/lib/catalog";

/** Findings a training run is the one paying for. The facet is computed by the same
 *  rules that computed the finding, so this is a filter rather than a judgement. */
export const trainingFindings = (d: Findings | null) =>
  (d?.findings ?? []).filter((f) => f.facets.includes("training"));

export function TrainingTab({ d, findings }: {
  d: TableDetail | null;
  findings: Findings | null;
}) {
  if (!d) return <Empty>reading the table…</Empty>;

  const mine = trainingFindings(findings);
  const warn = mine.filter((f) => f.severity === "warn").length;
  const note = mine.length - warn;

  const { meta_bytes, blob_bytes } = d.on_disk;
  const heavy = blob_bytes > 0;
  const frags = d.stats.num_fragments;

  return (
    <>
      <Verdict warn={warn} note={note} loaded={findings !== null} />

      {/* ------------------------------------------------------------ measured */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
        <Tile label="one epoch reads"
              value={<Bytes n={meta_bytes} />}
              foot={heavy
                ? <>the metadata half — the side files are not opened by a scan</>
                : <>no side files, so a pass reads the whole table</>} />

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
