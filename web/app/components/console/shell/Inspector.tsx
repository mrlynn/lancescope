"use client";

/** The right-hand pane: the facts that belong to no one screen.
 *
 *  Cost, capabilities, freshness and the findings index are all properties of the
 *  table you are looking at rather than of the panel you happen to have open, and
 *  every one of them was previously homeless — cost was a number in the header that
 *  each fetch overwrote, capabilities were rendered in exactly one place (the empty
 *  state), and freshness was not shown at all.
 *
 *  What this deliberately does *not* take is the finding cards. `Findings.tsx` puts
 *  them under the numbers they were computed from and says why: "put it three
 *  panels away from that number and the reader has to take it on trust; put it
 *  underneath, and they can check it." So this holds the *index* — what there is,
 *  how bad, and a way to get to the evidence — and the cards stay where they are.
 *  A tool whose argument is evidence before advice cannot move the advice away from
 *  the evidence to tidy a layout.
 */

import { useState } from "react";

import Icon from "@/app/components/Icon";
import { Bytes, Empty } from "@/app/components/console/atoms";
import type { Capability, Finding, RootCapabilities } from "@/app/lib/catalog";
import { type Workspace, totals } from "@/app/lib/workspace";

type Pane = "findings" | "cost" | "connection";

const PANES: { id: Pane; label: string }[] = [
  { id: "findings", label: "findings" },
  { id: "cost", label: "cost" },
  { id: "connection", label: "connection" },
];

export default function Inspector({ w, table, onGoToPanel }: {
  w: Workspace;
  table: string | null;
  /** Jump to the panel holding a finding's evidence. The whole reason the index is
   *  worth having separately from the cards. */
  onGoToPanel: (panel: Finding["panel"]) => void;
}) {
  const [pane, setPane] = useState<Pane>("findings");
  const current = w.list?.tables.find((t) => t.name === table) ?? null;
  const warn = (w.findings?.summary.warn ?? 0) > 0;

  return (
    <aside className="console-pane console-inspector" aria-label="Inspector">
      {/* Freshness first and never behind a tab. A table that has moved since you
          read it is the one thing here that changes what everything else means. */}
      <Freshness name={table} version={current?.version ?? null}
                 latest={current?.latest_version ?? null} />

      <div className="seg mb-4" role="tablist">
        {PANES.map((p) => (
          <button
            key={p.id}
            role="tab"
            aria-selected={pane === p.id}
            data-on={pane === p.id}
            onClick={() => setPane(p.id)}
            className="mono !px-2.5 text-[10px] tracking-[0.14em] uppercase"
          >
            {p.label}
            {p.id === "findings" && (w.findings?.summary.total ?? 0) > 0 && (
              <span className="ml-1" style={{ color: warn ? "var(--video)" : "var(--index)" }}>
                {w.findings?.summary.total}
              </span>
            )}
          </button>
        ))}
      </div>

      {pane === "findings" && <FindingsIndex w={w} onGoToPanel={onGoToPanel} />}
      {pane === "cost" && <CostLedger w={w} />}
      {pane === "connection" && <Connection caps={w.list?.capabilities ?? null} />}
    </aside>
  );
}

/** Which version you are reading, and whether it is still the newest. */
function Freshness({ name, version, latest }: {
  name: string | null; version: number | null; latest: number | null;
}) {
  if (!name) return null;
  const stale = version !== null && latest !== null && version < latest;
  return (
    <div className="mb-4 pb-4" style={{ borderBottom: "1px solid var(--rule)" }}>
      <div className="mono text-[12px] text-[var(--bright)] truncate">{name}</div>
      {version !== null && (
        <div className="mono text-[10px] text-[var(--haze)] mt-1">
          reading v{version}
          {latest !== null && latest !== version && ` of v${latest}`}
        </div>
      )}
      {stale && (
        <div className="mono text-[10px] mt-1.5 flex items-center gap-1.5"
             style={{ color: "var(--video)" }}>
          <Icon name="warning" size={12} />
          written to since this was read
        </div>
      )}
    </div>
  );
}

/** What there is to know, and where the numbers behind it are. */
function FindingsIndex({ w, onGoToPanel }: {
  w: Workspace; onGoToPanel: (panel: Finding["panel"]) => void;
}) {
  const d = w.findings;
  if (!d) return <Empty>working out what this table has to say…</Empty>;
  if (!d.findings.length) {
    return (
      <Empty>
        {d.partial_analysis
          ? "Of the rules that ran, none fired."
          : "Every rule this console knows was checked and none of them fired."}
      </Empty>
    );
  }
  return (
    <div className="space-y-1.5">
      {d.partial_analysis && (
        <p className="mono text-[10px] mb-2" style={{ color: "var(--video)" }}>
          one check could not run — see the panel
        </p>
      )}
      {d.findings.map((f) => (
        <button
          key={f.id}
          onClick={() => onGoToPanel(f.panel)}
          className="w-full text-left rounded-sm px-3 py-2 transition-colors"
          style={{
            border: `1px solid rgb(var(--${f.severity === "warn" ? "video" : "index"}-rgb) / 0.35)`,
            background: `rgb(var(--${f.severity === "warn" ? "video" : "index"}-rgb) / 0.05)`,
          }}
        >
          <span className="mono text-[11px] flex items-center gap-1.5"
                style={{ color: f.severity === "warn" ? "var(--video)" : "var(--index)" }}>
            <Icon name={f.severity === "warn" ? "warning" : "info"} size={12} />
            {f.title}
          </span>
          {/* Where its evidence is, because that is what the click does. */}
          <span className="mono text-[9px] text-[var(--dim)] uppercase tracking-[0.14em]
                           mt-1 block">
            {f.panel}
          </span>
        </button>
      ))}
    </div>
  );
}

/** Every read this console has made, and what each one cost.
 *
 *  A ledger rather than a number. The header showed the last read and discarded the
 *  rest, which for a tool whose argument is that reads have prices was throwing away
 *  the argument.
 */
function CostLedger({ w }: { w: Workspace }) {
  const sum = totals();
  if (!w.ledger.length) return <Empty>nothing read yet.</Empty>;
  return (
    <div>
      <div className="mono text-[11px] mb-1">
        <Bytes n={sum.bytes} tone="index" />
        <span className="text-[var(--haze)]"> · {sum.iops.toLocaleString()} iops</span>
      </div>
      {/* The scope, said rather than assumed — the same scope `TokenSpend` names for
          tokens. A total whose boundary is unstated is a number people misquote. */}
      <p className="text-[10px] text-[var(--haze)] mb-3">
        since this console opened. Nothing here is persisted.
      </p>
      <div className="space-y-0.5">
        {w.ledger.map((e, i) => (
          <div key={`${e.at}-${i}`}
               className="mono text-[10px] flex items-baseline justify-between gap-2">
            <span className="text-[var(--haze)] truncate">{e.label}</span>
            <Bytes n={e.bytes} tone={e.bytes === 0 ? undefined : "index"} />
          </div>
        ))}
      </div>
    </div>
  );
}

/** What this connection can and cannot do, with the reason for each.
 *
 *  Three states rather than two, because the server reports three: unsupported and
 *  never-verified are different claims, and flattening them would make the console
 *  say something the adapter did not.
 */
function Connection({ caps }: { caps: RootCapabilities | null }) {
  if (!caps) return <Empty>no connection reported.</Empty>;
  const rows: [string, Capability][] = [
    ["discover", caps.discover], ["inspect", caps.inspect],
    ["disk split", caps.disk_split], ["io meter", caps.io_meter],
    ["column bytes", caps.column_bytes],
  ];
  return (
    <div className="space-y-2.5">
      {rows.map(([label, c]) => (
        <div key={label}>
          <div className="mono text-[10px] flex items-center justify-between gap-2">
            <span className="text-[var(--haze)]">{label}</span>
            <span style={{
              color: c.state === "available" ? "var(--index)"
                : c.state === "unsupported" ? "var(--video)" : "var(--dim)",
            }}>
              {c.state}
            </span>
          </div>
          {c.reason && (
            <p className="text-[10px] text-[var(--dim)] leading-relaxed mt-0.5">{c.reason}</p>
          )}
        </div>
      ))}
    </div>
  );
}
