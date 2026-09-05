"use client";

/** Checks that read the data — quoted, chosen, run, and stoppable.
 *
 *  The panel is arranged around the decision rather than around the checks. Nothing
 *  runs on open: the first thing on screen is what each check *would* read, which is
 *  metadata work and costs kilobytes, and the run button appears once somebody has
 *  looked at the bill.
 *
 *  Findings render through the same `FindingCard` the metadata sweep uses, because
 *  they are the same kind of claim carrying the same kind of evidence. What differs
 *  is that each one arrives under the price it cost, and that is the point of the
 *  whole tab.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import Icon from "@/app/components/Icon";
import { FindingCard } from "@/app/components/console/Findings";
import { Empty } from "@/app/components/console/atoms";
import { fmtBytes } from "@/app/lib/api";
import { ApiError } from "@/app/lib/catalog";
import {
  LIVE_STATES, type CheckPlan, type ScanJob, type ScanPlan, type Selection,
  cancelScanJob, getScanJob, planScan, startScan,
} from "@/app/lib/datascan";

// Reading a job reads an in-memory object on the server and costs no dataset read,
// so this can poll briskly. Same call `web/app/console/new/page.tsx` made about an
// ingest, at a fraction of the duration.
const POLL_MS = 400;

const BTN = "btn mono !h-[26px] !px-2.5 text-[10px] tracking-[0.14em] uppercase";

function bytes(n: number): string {
  const b = fmtBytes(n);
  return `${b.value} ${b.unit}`;
}

export function DataTab({ table }: { table: string }) {
  const [plan, setPlan] = useState<ScanPlan | null>(null);
  const [chosen, setChosen] = useState<Record<string, string[]>>({});
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [job, setJob] = useState<ScanJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // One effect, keyed on the choices, because the whole promise is that the number on
  // screen describes the read that is about to happen: change a column and the quote
  // has to move with it. `alive` is the same guard the console page uses — a reply
  // that lands after the table changed would price the wrong table.
  useEffect(() => {
    let alive = true;
    const selections: Selection[] = Object.entries(chosen)
      .filter(([, cols]) => cols.length)
      .map(([check, columns]) => ({ check, columns }));
    const ask = async () => {
      try {
        const p = await planScan(table, selections);
        if (!alive) return;
        setPlan(p);
        setError(null);
        setPicked((was) => {
          // Everything that can run, pre-ticked on first sight. The bill is on
          // screen beside it, so the default being "all of it" is an offer rather
          // than a trap.
          if (!was.size) {
            return new Set(p.checks.filter((c) => c.capability.available)
                                   .map((c) => c.check));
          }
          // And afterwards: a check somebody just made runnable by naming the column
          // it asked for. Going to that trouble is the opt-in — leaving it unticked
          // would answer a request with a second request.
          const next = new Set(was);
          for (const c of p.checks) {
            if (c.capability.available && selections.some((sel) => sel.check === c.check)) {
              next.add(c.check);
            }
          }
          return next;
        });
      } catch (e) {
        if (alive) setError(e instanceof ApiError ? e.message : String(e));
      }
    };
    ask();
    return () => { alive = false; };
  }, [table, chosen]);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const follow = useCallback((id: string) => {
    const tick = async () => {
      try {
        const j = await getScanJob(id);
        setJob(j);
        if (LIVE_STATES.includes(j.state)) timer.current = setTimeout(tick, POLL_MS);
      } catch (e) {
        setError(e instanceof ApiError ? e.message : String(e));
      }
    };
    tick();
  }, []);

  const run = async () => {
    // Only what can actually run. A check ticked before a column choice made it
    // available stays ticked, and sending it would ask the server to refuse
    // something the screen had already said was refused.
    const runnable = new Set(available.map((c) => c.check));
    const selections = [...picked].filter((c) => runnable.has(c)).map((check) => ({
      check, columns: chosen[check] ?? [],
    }));
    setBusy(true); setError(null); setJob(null);
    try {
      const started = await startScan(table, selections);
      setJob(started);
      follow(started.id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!plan && !error) return <Empty>working out what checking this would cost…</Empty>;

  const available = plan?.checks.filter((c) => c.capability.available) ?? [];
  const total = available
    .filter((c) => picked.has(c.check) && c.estimate)
    .reduce((n, c) => n + Math.max(c.estimate!.bytes, c.estimate!.floor_bytes), 0);
  const unpriced = available.filter((c) => picked.has(c.check) && !c.estimate);
  const chosenCount = available.filter((c) => picked.has(c.check)).length;
  const live = job !== null && LIVE_STATES.includes(job.state);

  return (
    <>
      <p className="text-[13px] text-[var(--body)] leading-relaxed mb-5 max-w-[68ch]">
        Everything on the other tabs is derived from manifests and costs kilobytes.
        These read your columns. So nothing here has run: below is what each check
        would read, weighed from the file footers without opening a page of them, and
        the run button spends it only when you press it.
      </p>

      {error && (
        <div className="mono flex items-center gap-2.5 text-[12px] px-3.5 py-3 rounded-sm mb-4"
             style={{ background: "rgb(var(--video-rgb) / 0.12)",
                      border: "1px solid rgb(var(--video-rgb) / 0.4)",
                      color: "var(--video)" }}>
          <Icon name="warning" size={15} />
          {error}
        </div>
      )}

      {/* One list, refused checks included. Two lists was the obvious arrangement and
          the wrong one: `class-balance` is refused *because nobody has named a
          column*, and putting it in a section with no column picker in it left the
          reader looking at a request they had no way to answer. */}
      <div className="space-y-2">
        {(plan?.checks ?? []).map((c) => (
          <Quote key={c.check} c={c} picked={picked.has(c.check)}
                 columns={chosen[c.check] ?? c.columns}
                 all={plan?.survey.columns ?? []}
                 disabled={live}
                 onToggle={() => setPicked((was) => {
                   const next = new Set(was);
                   if (next.has(c.check)) next.delete(c.check); else next.add(c.check);
                   return next;
                 })}
                 onColumns={(cols) => setChosen((was) => ({ ...was, [c.check]: cols }))} />
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-3 mt-5">
        <button className="btn btn-accent mono text-[10px] tracking-[0.14em] uppercase"
                onClick={run} disabled={busy || live || chosenCount === 0}>
          <Icon name="spark" size={14} />
          {live ? "reading…" : `run ${chosenCount} check${chosenCount === 1 ? "" : "s"}`}
        </button>
        {live && job && (
          <button className={BTN} onClick={() => cancelScanJob(job.id).then(setJob)}>
            <Icon name="close" size={12} />stop
          </button>
        )}
        <span className="mono text-[11px] text-[var(--haze)]">
          {chosenCount === 0 ? "nothing selected" : <>
            about <span style={{ color: "var(--index)" }}>{bytes(total)}</span>
            {unpriced.length > 0 && `, plus ${unpriced.length} that cannot be weighed`}
          </>}
        </span>
      </div>

      {live && job && (
        <p className="mono text-[11px] text-[var(--haze)] mt-3">
          {job.progress.checks_done} of {job.progress.checks_total} done
          {job.progress.current && ` · running ${job.progress.current}`}
          {" · "}
          <span style={{ color: "var(--index)" }}>{bytes(job.read_bytes)}</span> so far.
          {" "}Stopping stops the work — this is the one place in the console where it
          does.
        </p>
      )}

      {job && !live && <Results job={job} />}

      <p className="text-[12px] text-[var(--haze)] leading-relaxed mt-8 max-w-[68ch]">
        What none of these can tell you: whether a label is <em>right</em>; whether two
        rows are the same thing when the embedding does not say so; what caused a
        distribution to move. Near-duplicates are approximate twice over — a sample
        rather than the table, and an index that returns close neighbours rather than
        the closest.
      </p>
    </>
  );
}

function Quote({ c, picked, columns, all, disabled, onToggle, onColumns }: {
  c: CheckPlan;
  picked: boolean;
  columns: string[];
  all: { name: string; scalar: boolean; blob: boolean; vector_dim: number | null }[];
  disabled: boolean;
  onToggle: () => void;
  onColumns: (cols: string[]) => void;
}) {
  const [editing, setEditing] = useState(false);
  const runnable = c.capability.available;
  const lit = picked && runnable;
  return (
    <div className="rounded-sm border px-4 py-3"
         style={{ borderColor: lit ? "rgb(var(--index-rgb) / 0.5)" : "var(--rule)",
                  background: lit ? "rgb(var(--index-rgb) / 0.05)" : "transparent" }}>
      <label className={`flex items-start gap-3 ${runnable ? "cursor-pointer" : ""}`}>
        <input type="checkbox" checked={picked && runnable} disabled={disabled || !runnable}
               onChange={onToggle} className="mt-1" />
        <span className="min-w-0 flex-1">
          <span className="mono text-[12px]"
                style={{ color: runnable ? "var(--bright)" : "var(--haze)" }}>
            {c.check}
          </span>
          <span className="text-[12px] text-[var(--haze)]"> — {c.title.toLowerCase()}</span>
          {runnable ? (
            <span className="block mono text-[11px] mt-1" style={{ color: "var(--index)" }}>
              {c.quote || c.estimate_reason}
            </span>
          ) : (
            // The reason, not a grey row. Half of these refusals are a request for
            // a column name, and the picker below is how it gets answered.
            <span className="block text-[12px] text-[var(--haze)] leading-relaxed mt-1">
              {c.capability.reason}
            </span>
          )}
        </span>
      </label>
      <div className="flex flex-wrap items-center gap-2 mt-2 pl-7">
        <span className="mono text-[10px] text-[var(--haze)]">
          {columns.length ? columns.join(", ") : "no columns chosen"}
        </span>
        <button className="mono text-[10px] text-[var(--haze)] hover:text-[var(--bright)]"
                onClick={() => setEditing((e) => !e)} disabled={disabled}>
          {editing ? "done" : "change"}
        </button>
      </div>
      {editing && (
        <div className="flex flex-wrap gap-1.5 mt-2 pl-7">
          {all.map((col) => {
            const on = columns.includes(col.name);
            return (
              <button key={col.name}
                      className={`mono text-[10px] px-2 py-1 rounded-sm border ${
                        on ? "text-[var(--bright)]" : "text-[var(--haze)]"}`}
                      style={{ borderColor: on ? "var(--index)" : "var(--rule)" }}
                      onClick={() => onColumns(on
                        ? columns.filter((n) => n !== col.name)
                        : [...columns, col.name])}>
                {col.name}
              </button>
            );
          })}
        </div>
      )}
      {/* The caveats belong beside the number rather than in a footnote: they are the
          reasons the number is a weight and not a ceiling. */}
      {lit && c.estimate?.caveats?.length ? (
        <ul className="mt-2 pl-7 space-y-1">
          {c.estimate.caveats.map((v) => (
            <li key={v} className="text-[11px] text-[var(--haze)] leading-relaxed">{v}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function Results({ job }: { job: ScanJob }) {
  return (
    <section className="mt-7">
      <div className="mono text-[11px] text-[var(--haze)] mb-3">
        version {job.version} · {job.state} ·{" "}
        <span style={{ color: "var(--index)" }}>{bytes(job.read_bytes)}</span> in{" "}
        {job.read_iops.toLocaleString()} IOs
        {job.detail && ` · ${job.detail}`}
      </div>
      {job.results.map((r) => (
        <div key={r.check} className="mb-5">
          <div className="mono text-[11px] text-[var(--haze)] mb-2">
            {r.check} · {r.state} ·{" "}
            <span style={{ color: "var(--index)" }}>{bytes(r.read_bytes)}</span> ·{" "}
            {r.ms.toLocaleString()} ms
          </div>
          {r.state !== "done" && r.detail && (
            <p className="text-[12px] text-[var(--body)] leading-relaxed mb-2">
              {r.detail}
            </p>
          )}
          <div className="space-y-3">
            {r.findings.map((f) => <FindingCard key={f.id} f={f} />)}
          </div>
        </div>
      ))}
      {job.findings.length === 0 && job.state === "done" && (
        <Empty>Every check ran and none of them found anything to say.</Empty>
      )}
    </section>
  );
}
