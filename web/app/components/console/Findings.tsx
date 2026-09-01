"use client";

/** Findings, rendered where their evidence is.
 *
 *  A finding is a sentence about a number. Put it three panels away from that number
 *  and the reader has to take it on trust; put it underneath, and they can check it.
 *  So the same list renders in two places — inline under the panel that owns each
 *  finding, and collected in Insights — from one fetch. */

import Icon from "@/app/components/Icon";
import { Empty } from "@/app/components/console/atoms";
import type { Finding, Findings } from "@/app/lib/catalog";

const TONE = {
  warn: { rgb: "var(--video-rgb)", color: "var(--video)", icon: "warning" },
  note: { rgb: "var(--index-rgb)", color: "var(--index)", icon: "info" },
} as const;

/** Numbers are formatted by what they are: bytes read as bytes, a share as a
 *  percentage, everything else grouped. An evidence block full of `1114000000` is
 *  evidence nobody checks. */
function fmtValue(key: string, value: unknown): string {
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (Array.isArray(value)) return value.join(", ") || "none";
  if (typeof value !== "number") return String(value ?? "—");
  if (key.endsWith("_bytes")) {
    if (value >= 1e9) return `${(value / 1e9).toFixed(2)} GB`;
    if (value >= 1e6) return `${(value / 1e6).toFixed(1)} MB`;
    if (value >= 1e3) return `${(value / 1e3).toFixed(1)} KB`;
    return `${value} B`;
  }
  if (key === "share" || key === "coverage") return `${(value * 100).toFixed(1)}%`;
  return value.toLocaleString();
}

export function FindingCard({ f, compact = false }: { f: Finding; compact?: boolean }) {
  const tone = TONE[f.severity] ?? TONE.note;
  return (
    <div
      className="rounded-sm border px-4 py-3.5"
      style={{
        borderColor: `rgb(${tone.rgb} / 0.4)`,
        background: `rgb(${tone.rgb} / 0.06)`,
      }}
    >
      <div className="flex items-center gap-2 mono text-[12px]" style={{ color: tone.color }}>
        <Icon name={tone.icon} size={14} />
        {f.title}
      </div>

      <p className="text-[13px] text-[var(--body)] leading-relaxed mt-2">{f.claim}</p>

      {f.caveat && (
        <p className="text-[12px] leading-relaxed mt-2.5 pl-3"
           style={{ color: "var(--bright)", borderLeft: "2px solid rgb(var(--index-rgb) / 0.5)" }}>
          {f.caveat}
        </p>
      )}

      {f.suggested_action && (
        <p className="text-[12px] text-[var(--haze)] leading-relaxed mt-2.5">
          {f.suggested_action}
        </p>
      )}

      {!compact && (
        <dl className="flex flex-wrap gap-x-6 gap-y-1 mt-3 pt-3"
            style={{ borderTop: "1px solid var(--hairline)" }}>
          {Object.entries(f.evidence).map(([k, v]) => (
            <div key={k} className="flex items-baseline gap-1.5">
              <dt className="eyebrow">{k.replace(/_/g, " ")}</dt>
              <dd className="mono text-[11px] text-[var(--bright)]">{fmtValue(k, v)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

/** "One check could not run" — its own state, never folded into "nothing to report".
 *
 *  A rule that raises used to be swallowed, which made a broken check look like a
 *  clean table. Anything derived from a partial analysis has to say so, including
 *  the language layer when it eventually narrates this. */
export function PartialAnalysis({ d }: { d: Findings | null }) {
  if (!d?.partial_analysis) return null;
  const n = d.failed_rules.length;
  return (
    <div className="rounded-sm border px-4 py-3 mt-4"
         style={{ borderColor: "rgb(var(--video-rgb) / 0.4)",
                  background: "rgb(var(--video-rgb) / 0.06)" }}>
      <div className="flex items-center gap-2 mono text-[12px]" style={{ color: "var(--video)" }}>
        <Icon name="warning" size={14} />
        {n} check{n === 1 ? "" : "s"} could not run
      </div>
      <p className="text-[12px] text-[var(--body)] leading-relaxed mt-2">
        This analysis is incomplete. What is shown below is still derived from real
        metadata — but something this console normally checks was skipped, so the
        absence of a finding is not evidence of its absence.
      </p>
      <ul className="mt-2 space-y-1">
        {d.failed_rules.map((f) => (
          <li key={f.rule} className="mono text-[10px] text-[var(--haze)]">
            {f.rule} — {f.error}: {f.message}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The findings belonging to one panel, under that panel's own numbers. */
export function PanelFindings({ d, panel }: { d: Findings | null; panel: Finding["panel"] }) {
  const mine = (d?.findings ?? []).filter((f) => f.panel === panel);
  if (!mine.length) return null;
  return (
    <div className="mt-6 space-y-3">
      {mine.map((f) => <FindingCard key={f.id} f={f} compact />)}
    </div>
  );
}

/** Everything, in one place, for reading rather than for checking a number. */
export function InsightsTab({ d }: { d: Findings | null }) {
  if (!d) return <Empty>working out what this table has to say…</Empty>;

  if (!d.findings.length) {
    return (
      <>
        <PartialAnalysis d={d} />
        <Empty>
          Nothing to report on <span className="mono text-[var(--bright)]">{d.name}</span>.
          {d.partial_analysis
            ? " Of the rules that ran, none fired — see above for the ones that did not."
            : " Every rule this console knows was checked and none of them fired."}
        </Empty>
      </>
    );
  }

  const { warn, note } = d.summary;
  return (
    <>
      <p className="text-[13px] text-[var(--body)] leading-relaxed mb-5 max-w-[68ch]">
        {warn > 0 && `${warn} thing${warn === 1 ? "" : "s"} worth acting on`}
        {warn > 0 && note > 0 && ", and "}
        {note > 0 && `${note} worth knowing`}
        {". "}
        Every one of these is derived from the metadata on the other tabs — no model
        was asked, and nothing here cost a token. Each also appears under the panel
        holding the numbers it was computed from.{d.partial_analysis
          ? " This sweep was incomplete; see below."
          : ""}
      </p>

      <PartialAnalysis d={d} />

      <div className="space-y-3 mt-4">
        {d.findings.map((f) => <FindingCard key={f.id} f={f} />)}
      </div>
    </>
  );
}
