"use client";

/** What should be done about this table, as a document you read before you act.
 *
 *  The one screen in this console about changing something, and it changes nothing.
 *  Every plan is arithmetic over metadata the console already read — which fragments,
 *  how many bytes, whether it can be undone — and the only way out of here is the
 *  copy button. That is not a limitation waiting to be lifted: a workbench whose
 *  claim is that browsing costs kilobytes and changes nothing does not get to also
 *  rewrite your table because a panel had a button.
 *
 *  The blocked plans matter more than the ready ones. "This table has 1,114 rows and
 *  an approximate index wants 5,000" is the answer, not a greyed-out row — so a
 *  precondition that does not hold is rendered as prose, in place, rather than
 *  hidden behind a disabled state. */

import { useCallback, useEffect, useState } from "react";
import Icon from "@/app/components/Icon";
import { Bytes, Caveat, Copy, Cost, Empty, Eyebrow } from "@/app/components/console/atoms";
import {
  KINDS, type Kind, type OperationPlan, type PlanSummary, type Proposals,
  buildPlan, getProposals,
} from "@/app/lib/ops";

/** Operations needing an argument the console cannot guess. They are offered, and
 *  they say what they are waiting for rather than failing when asked. */
const NEEDS_ARGUMENT: Partial<Record<Kind, string>> = {
  "index": "a column",
  "restore": "a version to go back to",
  "migrate-copy": "a destination",
  "migrate-schema": "the columns to drop or retype",
  "migrate-embed": "a vector column",
};

export function Operations({ table }: { table: string }) {
  const [proposals, setProposals] = useState<Proposals | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<OperationPlan | null>(null);
  const [busy, setBusy] = useState(false);

  // No reset on `table` changing: the panel is mounted with `key={table}`, so a
  // different table is a different component and the state goes with the old one.
  // Clearing it here as well would be the same effect twice, and the second one is
  // the one that renders a blank panel before the fetch it just cancelled.
  useEffect(() => {
    let live = true;
    getProposals(table)
      .then((d) => { if (live) setProposals(d); })
      .catch((e) => { if (live) setError(e instanceof Error ? e.message : "failed"); });
    return () => { live = false; };
  }, [table]);

  const show = useCallback(async (kind: Kind, extra: Record<string, unknown> = {}) => {
    setBusy(true);
    setError(null);
    try {
      setOpen(await buildPlan(table, { kind, ...extra }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not build that plan");
    } finally {
      setBusy(false);
    }
  }, [table]);

  if (error && !proposals) return <Empty>{error}</Empty>;
  if (!proposals) return <Empty>working out what is worth doing…</Empty>;

  return (
    <>
      <p className="text-[12px] text-[var(--haze)] mb-5 leading-relaxed">
        What an operation would do to <span className="mono">{table}</span>, computed
        from version {proposals.version}. Nothing here runs — each plan carries the
        command, and where it goes next is yours.
      </p>

      <Eyebrow>Worth considering</Eyebrow>
      {proposals.proposals.length === 0 ? (
        <Empty>
          Nothing about this table&rsquo;s findings suggests an operation. That is an
          answer, not an empty list.
        </Empty>
      ) : (
        <div className="space-y-3 mb-6">
          {proposals.proposals.map((p) => (
            <Proposal key={p.id} p={p} onOpen={show} />
          ))}
        </div>
      )}

      <Eyebrow>Plan something else</Eyebrow>
      {/* `.72`, not `.4` — the figure every other disabled control in this console
          uses. At .4 over --haze these labels land near 2:1, and a control you cannot
          read is not the same as one you cannot press. */}
      <div className="flex flex-wrap gap-2 mb-2">
        {KINDS.map((k) => (
          <button
            key={k}
            disabled={busy || Boolean(NEEDS_ARGUMENT[k])}
            onClick={() => show(k)}
            title={NEEDS_ARGUMENT[k]
              ? `Needs ${NEEDS_ARGUMENT[k]} — ask for it under Ask, or open it from a proposal`
              : `Plan a ${k}`}
            className="mono text-[11px] px-2.5 py-1.5 rounded-sm border border-[var(--rule)]
                       text-[var(--haze)] disabled:opacity-[.72] hover:text-[var(--bright)]"
          >
            {k}
          </button>
        ))}
      </div>
      <p className="text-[11px] text-[var(--dim)] mb-6">
        The greyed ones need an argument — a column, a version, a destination. Ask for
        one under Ask and it will name the argument it used.
      </p>

      {error && <Caveat>{error}</Caveat>}
      {open && <PlanView plan={open} onClose={() => setOpen(null)} />}
    </>
  );
}

function Proposal({ p, onOpen }: {
  p: PlanSummary;
  onOpen: (kind: Kind, extra?: Record<string, unknown>) => void;
}) {
  const refused = !p.capability.available;
  return (
    <div
      className="panel p-4"
      style={refused
        ? { borderColor: "rgb(var(--video-rgb) / 0.45)" }
        : undefined}
    >
      <div className="flex items-baseline justify-between gap-3 mb-2">
        <span className="text-[13px] text-[var(--bright)]">{p.title}</span>
        <span className="eyebrow shrink-0">{p.kind}</span>
      </div>
      <p className="text-[12px] text-[var(--haze)] leading-relaxed mb-3">{p.summary}</p>

      {refused ? (
        <p className="text-[12px] leading-relaxed" style={{ color: "var(--video)" }}>
          {p.capability.reason}
        </p>
      ) : (
        <>
          {p.blocked_by.map((c) => (
            <p key={c.claim} className="text-[12px] leading-relaxed mb-2"
               style={{ color: "var(--video)" }}>
              <Icon name="warning" size={12} /> {c.claim} — {c.detail}
            </p>
          ))}
          <div className="flex items-center gap-3 flex-wrap">
            <button
              onClick={() => onOpen(p.kind as Kind, argumentsFor(p))}
              className="mono text-[11px] px-2.5 py-1.5 rounded-sm border border-[var(--rule)]
                         text-[var(--haze)] hover:text-[var(--bright)]"
            >
              open the plan
            </button>
            <Reversible ok={p.reversible} />
          </div>
        </>
      )}
    </div>
  );
}

/** Re-open a proposal as a full plan. The summary carries the arguments the proposal
 *  was built with inside `affected`, which is where they were computed — asking the
 *  user to retype a column the console already chose would be theatre. */
function argumentsFor(p: PlanSummary): Record<string, unknown> {
  const columns = p.affected.columns;
  if (p.kind === "index" && Array.isArray(columns) && columns.length > 0) {
    return { column: String(columns[0]) };
  }
  return {};
}

function Reversible({ ok }: { ok: boolean }) {
  return (
    <span className="mono text-[10px]" style={{ color: ok ? "var(--index)" : "var(--video)" }}>
      {ok ? "reversible" : "CANNOT BE UNDONE"}
    </span>
  );
}

function PlanView({ plan, onClose }: { plan: OperationPlan; onClose: () => void }) {
  const e = plan.estimate;
  return (
    <div className="panel p-5 mt-2" style={{ borderColor: "var(--rule)" }}>
      <div className="flex items-baseline justify-between gap-3 mb-1">
        <span className="text-[14px] text-[var(--bright)]">{plan.title}</span>
        <button className="iconbtn shrink-0" onClick={onClose} aria-label="Close plan">
          <Icon name="close" size={14} />
        </button>
      </div>
      <p className="text-[12px] text-[var(--haze)] leading-relaxed mb-4">{plan.summary}</p>

      {plan.stale && (
        <Caveat>
          This was computed against version {plan.target_version} and the table is now
          on {plan.current_version}. The affected set below is arithmetic about a table
          that has moved — build it again.
        </Caveat>
      )}

      {!plan.capability.available ? (
        <p className="text-[12px] leading-relaxed" style={{ color: "var(--video)" }}>
          {plan.capability.reason}
        </p>
      ) : (
        <>
          <Eyebrow>Before it runs</Eyebrow>
          <div className="space-y-1.5 mb-5">
            {plan.preconditions.map((c) => (
              <div key={c.claim} className="text-[12px] leading-relaxed flex gap-2">
                <span className="shrink-0 mt-[2px]"
                      style={{ color: c.holds ? "var(--index)" : "var(--video)" }}>
                  <Icon name={c.holds ? "check" : "warning"} size={12} />
                </span>
                <span>
                  <span className="text-[var(--body)]">{c.claim}</span>
                  {c.detail && <span className="text-[var(--haze)]"> — {c.detail}</span>}
                </span>
              </div>
            ))}
          </div>

          <Eyebrow>What it moves</Eyebrow>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-2">
            <Figure label="reads" bytes={e.read_bytes} />
            <Figure label="writes" bytes={e.write_bytes} />
            <Figure label="disk" bytes={e.disk_delta_bytes} signed />
            <div className="panel px-4 py-3">
              <div className="eyebrow mb-1">undo</div>
              <div className="mono text-[13px]">
                <Reversible ok={plan.reversible} />
              </div>
            </div>
          </div>
          <p className="text-[11px] text-[var(--dim)] mb-5 leading-relaxed">{e.basis}</p>

          <Eyebrow>Affected</Eyebrow>
          <div className="mono text-[12px] text-[var(--haze)] mb-5 space-y-1">
            {Object.entries(plan.affected).map(([k, v]) => (
              <div key={k}>
                <span className="text-[var(--dim)]">{k}</span>{" "}
                {typeof v === "number" ? v.toLocaleString() : JSON.stringify(v)}
              </div>
            ))}
          </div>

          <Eyebrow>Rolling back</Eyebrow>
          <p className="text-[12px] text-[var(--haze)] leading-relaxed mb-5">
            {plan.rollback}
          </p>

          {plan.verification && (
            <>
              <Eyebrow>Proving it worked</Eyebrow>
              <p className="text-[12px] text-[var(--haze)] leading-relaxed mb-5">
                {plan.verification}
              </p>
            </>
          )}

          {plan.script && (
            <>
              <div className="flex items-center justify-between mb-2">
                <Eyebrow>The command</Eyebrow>
                <Copy value={plan.script} what="the script" />
              </div>
              <pre className="mono text-[11px] leading-relaxed p-3 rounded-sm overflow-x-auto
                              bg-[var(--ink-3)] border border-[var(--rule)] text-[var(--body)]">
                {plan.script}
              </pre>
              <p className="text-[11px] text-[var(--dim)] mt-2 leading-relaxed">
                {plan.how_to_run}
              </p>
            </>
          )}
        </>
      )}

      {plan.caveats.map((c) => <Caveat key={c}>{c}</Caveat>)}

      <div className="mt-4 pt-3 border-t border-[var(--hairline)]">
        <Cost bytes={plan.read_bytes} iops={plan.read_iops} label="planning this read" />
      </div>
    </div>
  );
}

function Figure({ label, bytes, signed = false }: {
  label: string; bytes: number | null; signed?: boolean;
}) {
  return (
    <div className="panel px-4 py-3">
      <div className="eyebrow mb-1">{label}</div>
      <div className="mono text-[13px] text-[var(--bright)]">
        {bytes === null ? (
          // Not zero. A figure this console did not model is a gap, and rendering it
          // as 0 B would be stating a measurement nobody took.
          <span className="text-[var(--dim)]">not modelled</span>
        ) : bytes === 0 ? (
          <span className="text-[var(--haze)]">nothing</span>
        ) : (
          <>
            {signed && bytes < 0 && <span style={{ color: "var(--index)" }}>−</span>}
            <Bytes n={Math.abs(bytes)} tone={signed && bytes < 0 ? "index" : undefined} />
          </>
        )}
      </div>
    </div>
  );
}
