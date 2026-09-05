"use client";

/** Findings, rendered where their evidence is.
 *
 *  A finding is a sentence about a number. Put it three panels away from that number
 *  and the reader has to take it on trust; put it underneath, and they can check it.
 *  So the same list renders in two places — inline under the panel that owns each
 *  finding, and collected in Insights — from one fetch. */

import Link from "next/link";
import { type ReactNode, useCallback, useEffect, useState } from "react";
import Icon from "@/app/components/Icon";
import BundleButton from "@/app/components/console/BundleButton";
import { Empty } from "@/app/components/console/atoms";
import { fmtBytes } from "@/app/lib/api";
import type { Finding, Findings } from "@/app/lib/catalog";
import {
  type Capabilities, type TableSummary, type TokenMeter,
  getTokenMeter, resetTokenMeter, summariseTable,
} from "@/app/lib/settings";

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
export function InsightsTab({ d, table, ai }: {
  d: Findings | null;
  table: string | null;
  ai: Capabilities | null;
}) {
  // Bumped whenever something is asked of a model, so the meter below re-reads
  // rather than polling a number that changes only when someone acts.
  const [spent, setSpent] = useState(0);

  if (!d) return <Empty>working out what this table has to say…</Empty>;

  if (!d.findings.length) {
    return (
      <>
        {table && <Summary table={table} ai={ai} partial={d.partial_analysis}
                           onSpend={() => setSpent((n) => n + 1)} />}
        <PartialAnalysis d={d} />
        <Empty>
          Nothing to report on <span className="mono text-[var(--bright)]">{d.name}</span>.
          {d.partial_analysis
            ? " Of the rules that ran, none fired — see above for the ones that did not."
            : " Every rule this console knows was checked and none of them fired."}
        </Empty>
        {table && <BundleButton table={table}
                                note="a clean sweep is worth being able to show too" />}
        <TokenSpend refreshKey={spent} />
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

      {table && <Summary table={table} ai={ai} partial={d.partial_analysis}
                         onSpend={() => setSpent((n) => n + 1)} />}

      <PartialAnalysis d={d} />

      <div className="space-y-3 mt-4">
        {d.findings.map((f) => <FindingCard key={f.id} f={f} />)}
      </div>

      {table && <BundleButton table={table}
                              note="these findings with their evidence, the schema, the versions, the layout and the reader" />}

      <TokenSpend refreshKey={spent} />
    </>
  );
}

/** Where the provider, model and key live. The settings page reads its tab from the
 *  query string, so this lands on the panel rather than on the page above it.
 *
 *  Every sentence here that names the model, or says one is missing, points at the
 *  one screen that can change that — a model name the reader cannot act on is a
 *  dead end, and the reader is usually reading it because they want it different. */
function IntelLink({ children }: { children: ReactNode }) {
  return (
    <Link href="/console/settings?tab=intelligence"
          className="underline underline-offset-2 hover:text-[var(--bright)]"
          data-tip="Provider, model and key">
      {children}
    </Link>
  );
}

/** A description of the table in a few sentences, written from what the console
 *  already knows and cached against the version it describes.
 *
 *  Not fetched on render. It costs a model call the first time, and a panel that
 *  spends money because somebody clicked a tab is a panel that spends money nobody
 *  asked to spend. After that it is a file read, and says so. */
export function Summary({ table, ai, partial, onSpend }: {
  table: string;
  ai: Capabilities | null;
  partial: boolean;
  onSpend: () => void;
}) {
  const [state, setState] = useState<TableSummary | null>(null);
  const [busy, setBusy] = useState(false);

  if (!ai?.available) {
    return (
      <p className="text-[12px] text-[var(--haze)] leading-relaxed mb-4">
        A provider would add a written summary here — the findings below need none.
        {ai?.setup_hint ? ` ${ai.setup_hint}` : ""}{" "}
        <IntelLink>Configure one in settings</IntelLink>.
      </p>
    );
  }

  const ask = async (refresh: boolean) => {
    setBusy(true);
    try {
      setState(await summariseTable(table, refresh));
      onSpend();
    } catch (e) {
      setState({ ok: false, cached: false,
                 error: e instanceof Error ? e.message : "summary failed" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mb-5">
      {!state && (
        <div className="flex items-center gap-3 flex-wrap">
          <button className="btn mono text-[10px] tracking-[0.14em] uppercase"
                  onClick={() => ask(false)} disabled={busy}>
            <Icon name="spark" size={14} />
            {busy ? "writing…" : "Describe this table"}
          </button>
          <span className="mono text-[10px] text-[var(--haze)]">
            <IntelLink>{ai.models_by_role.deep.id}</IntelLink>, from the schema and the
            findings below — never the rows
          </span>
        </div>
      )}

      {busy && state === null && (
        <p className="mono text-[10px] text-[var(--haze)] mt-2">
          A large local model can take a minute. The answer is kept against this
          table version, so it is asked once.
        </p>
      )}

      {state && !state.ok && (
        <div className="mono flex items-start gap-2 text-[12px] px-3.5 py-3 rounded-sm"
             style={{ background: "rgb(var(--video-rgb) / 0.1)",
                      border: "1px solid rgb(var(--video-rgb) / 0.4)",
                      color: "var(--video)" }}>
          <Icon name="warning" size={14} />
          <span>
            {state.error}{state.setup_hint ? ` — ${state.setup_hint}` : ""}{" "}
            <IntelLink>Check the provider settings</IntelLink>.
          </span>
        </div>
      )}

      {state?.ok && (
        <div className="rounded-sm border px-4 py-3.5"
             style={{ borderColor: "var(--rule)", background: "var(--ink-3)" }}>
          <p className="text-[13px] text-[var(--body)] leading-relaxed">
            {state.summary}
          </p>
          {state.most_notable && (
            <p className="text-[13px] leading-relaxed mt-2.5"
               style={{ color: "var(--bright)" }}>
              {state.most_notable}
            </p>
          )}

          {partial && (
            <p className="text-[12px] leading-relaxed mt-2.5" style={{ color: "var(--video)" }}>
              Written while one of the console&rsquo;s checks could not run, so it describes
              an incomplete picture.
            </p>
          )}

          <div className="mono text-[10px] text-[var(--haze)] mt-3 flex flex-wrap items-center gap-x-2">
            {state.cached ? (
              <>kept from an earlier answer about v{state.version} — no model was
                asked, and nothing was spent</>
            ) : (
              <>
                {state.model} · {((state.ms ?? 0) / 1000).toFixed(1)}s ·{" "}
                {state.cost_usd === 0 ? "no cost, ran locally"
                  : state.cost_usd == null ? "cost unknown"
                  : `$${state.cost_usd.toFixed(5)}`}
                {state.context_read_bytes ? (
                  <> · {fmtBytes(state.context_read_bytes).value}{" "}
                    {fmtBytes(state.context_read_bytes).unit} read to describe it</>
                ) : null}
              </>
            )}
            <button className="underline hover:text-[var(--bright)]"
                    onClick={() => ask(true)} disabled={busy}>
              {busy ? "…" : "ask again"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/** What the language layer has spent since the server started.
 *
 *  The demo has a byte instrument because the interesting fact about a Lance search
 *  is how little it reads. This is the same argument one layer up. It shows nothing
 *  until something has been spent, because a row of zeros teaches people to stop
 *  looking at the row. */
export function TokenSpend({ refreshKey }: { refreshKey: number }) {
  const [m, setM] = useState<TokenMeter | null>(null);

  const load = useCallback(() => {
    getTokenMeter().then(setM).catch(() => setM(null));
  }, []);

  useEffect(() => { load(); }, [load, refreshKey]);

  if (!m || (m.calls === 0 && m.cache_hits === 0)) return null;

  const priced = m.calls - m.unpriced_calls;
  return (
    <div className="mt-6 pt-4" style={{ borderTop: "1px solid var(--hairline)" }}>
      <div className="flex items-center gap-3 flex-wrap">
        <span className="eyebrow">spent here</span>
        <span className="mono text-[12px] text-[var(--bright)]">
          {m.calls} call{m.calls === 1 ? "" : "s"} ·{" "}
          {(m.input_tokens + m.output_tokens).toLocaleString()} tokens
          {priced > 0 && m.cost_usd > 0 && (
            <> · <span style={{ color: "var(--index)" }}>${m.cost_usd.toFixed(4)}</span></>
          )}
        </span>
        <button className="mono text-[10px] text-[var(--haze)] hover:text-[var(--bright)]"
                onClick={() => resetTokenMeter().then(setM).catch(() => {})}>
          reset
        </button>
      </div>

      <p className="mono text-[10px] text-[var(--haze)] mt-1.5 leading-relaxed">
        {m.cache_hits > 0 && (
          <>{m.cache_hits} answer{m.cache_hits === 1 ? "" : "s"} came from the cache
            and cost nothing. </>
        )}
        {m.unpriced_calls > 0 && (
          <>{m.unpriced_calls} ran on a model with no published price — free if it is
            local, unknown otherwise. </>
        )}
        {m.ceiling_usd !== null && <>Ceiling ${m.ceiling_usd.toFixed(2)}. </>}
        Since this server started, {Math.max(1, Math.round(m.seconds / 60))} minute
        {Math.round(m.seconds / 60) === 1 ? "" : "s"} ago.
      </p>
    </div>
  );
}
