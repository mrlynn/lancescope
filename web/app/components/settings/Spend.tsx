"use client";

/** What the language layer has actually cost.
 *
 *  The footer meter answers "this process, since it started", which stops being the
 *  interesting number the second the server restarts. Somebody who has pasted an
 *  Anthropic key into this console has a different question — where did the money
 *  go — and that question is about days, tasks and models rather than about one
 *  uptime.
 *
 *  Three rules the whole panel is built on.
 *
 *  **Nothing here is estimated.** Every figure comes off a line written at the
 *  moment of the call. A model with no published price is drawn as unpriced, never
 *  as zero: one guessed number would make the other nine worthless.
 *
 *  **A saving is never spend.** Cache hits are counted apart and shown in their own
 *  colour. Adding what a hit avoided to the bill would be a lie in the flattering
 *  direction, and subtracting it would be one in the other.
 *
 *  **An empty panel says why it is empty.** A grid of zeroes teaches people to stop
 *  looking at the grid. */

import { useCallback, useEffect, useState } from "react";
import Icon from "@/app/components/Icon";
import { Eyebrow, Th, Td } from "@/app/components/console/atoms";
import {
  type SpendBucket, type SpendEvent, type SpendHistory,
  clearSpend, getSpend,
} from "@/app/lib/settings";

/* --------------------------------------------------------------------- format */

/** Dollars at the precision the number deserves.
 *
 *  A filter costs a fraction of a cent and a month of summaries costs a few
 *  dollars; one format cannot serve both without either rounding the small one to
 *  nothing or dragging six zeroes across the big one. */
function usd(n: number): string {
  if (n === 0) return "$0";
  if (n < 0.01) return `$${n.toFixed(4)}`;
  if (n < 10) return `$${n.toFixed(3)}`;
  return `$${n.toFixed(2)}`;
}

function tok(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

function ms(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}s` : `${n}ms`;
}

function clock(ts: number): string {
  return new Date(ts * 1000).toLocaleString(undefined, {
    month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

/* --------------------------------------------------------------------- colour */

/** One colour per task, held still across every chart on the panel.
 *
 *  The console has two accents and this needs four series, so the extra two are the
 *  same two moved along the lightness axis rather than new hues invented for a
 *  chart. Mixed toward `--bright` rather than faded with alpha, because alpha
 *  separates a series from its neighbour only against the background it was checked
 *  on — mixing toward the foreground colour separates it in both themes, since
 *  `--bright` is near-black in one and near-white in the other.
 *
 *  A task the colours do not know about falls back to body text, which is the honest
 *  way to say "this is real, and it is not one of the ones I was designed around".
 */
const TASK_COLOR: Record<string, string> = {
  summary: "var(--index)",
  filter: "var(--video)",
  ask: "color-mix(in oklab, var(--video) 55%, var(--bright))",
  selftest: "color-mix(in oklab, var(--index) 52%, var(--bright))",
};

const TASK_WHAT: Record<string, string> = {
  summary: "describing a table from its schema and findings",
  filter: "turning a plain-English question into a predicate",
  selftest: "the one round trip that proves the key works",
  ask: "the ask box, which runs a tool loop",
};

const color = (task: string) => TASK_COLOR[task] ?? "var(--body)";

/* ---------------------------------------------------------------------- panel */

const WINDOWS = [7, 30, 90] as const;

export default function Spend({ ceiling }: { ceiling?: number | null }) {
  const [days, setDays] = useState<number>(30);
  const [h, setH] = useState<SpendHistory | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // Bumped to ask again for the same window. The ledger is a file this server is
  // appending to, so "the same request" is a different answer a minute later.
  const [nonce, setNonce] = useState(0);
  const load = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let live = true;
    getSpend(days)
      .then((x) => { if (live) { setH(x); setErr(null); } })
      .catch((e) => {
        if (live) setErr(e instanceof Error ? e.message : "unreadable");
      });
    return () => { live = false; };
  }, [days, nonce]);

  if (err) {
    // A 404 here has one cause and it is not the ledger: the route is younger than
    // the server process answering for it. Saying "could not be read — 404" is true
    // and sends someone looking at a file that is fine.
    const stale = err === "404";
    return (
      <section className="panel p-6">
        <Eyebrow>Spend</Eyebrow>
        <p className="mono text-[12px] text-[var(--haze)] leading-relaxed">
          {stale
            ? "this route is not mounted — the API server predates it. Restart it (make api) and this panel will fill in."
            : `the ledger could not be read — ${err}`}
        </p>
      </section>
    );
  }

  if (!h) {
    return (
      <section className="panel p-6">
        <Eyebrow>Spend</Eyebrow>
        <p className="mono text-[12px] text-[var(--haze)]">reading the ledger…</p>
      </section>
    );
  }

  const t = h.totals;
  const spent = t.calls > 0 || t.cache_hits > 0;
  // The ceiling the server resolved wins: it reads the environment too, and a
  // half-typed number in the form above should not draw a line on a chart.
  const cap = h.ceiling_usd ?? ceiling ?? null;

  return (
    <section className="panel p-6">
      <div className="flex items-start justify-between gap-4 flex-wrap mb-4">
        <div className="min-w-[300px] flex-1">
          <Eyebrow>Spend</Eyebrow>
          <p className="text-[13px] text-[var(--body)] leading-relaxed max-w-[62ch]">
            Every call this console makes to a provider is written to a ledger on this
            machine — tokens, dollars, milliseconds and which task asked. Counts only:
            no question, no answer, no table name. It survives a restart, which the
            meter in the footer does not.
          </p>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {WINDOWS.map((d) => (
            <button key={d} className="btn"
                    style={d === days
                      ? { borderColor: "rgb(var(--index-rgb) / 0.55)", color: "var(--index)" }
                      : undefined}
                    onClick={() => setDays(d)}>
              {d}d
            </button>
          ))}
          <button className="iconbtn ml-1" title="re-read the ledger" onClick={load}>
            <Icon name="refresh" size={14} />
          </button>
        </div>
      </div>

      {!h.logging && (
        <p className="mono text-[11px] mb-4" style={{ color: "var(--video)" }}>
          LANCESCOPE_SPEND_LOG is off — nothing is being recorded, so this is history
          up to the moment it was switched off.
        </p>
      )}

      {!spent ? (
        <Nothing provider={h.provider} days={h.window_days} />
      ) : (
        <>
          <Stats h={h} />
          {cap !== null && <Ceiling spent={t.cost_usd} cap={cap} />}
          <Daily h={h} cap={cap} />
          <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,320px)_minmax(0,1fr)] gap-6 mt-8">
            <Mix by={h.by_task} total={t.cost_usd} days={h.window_days} />
            <Models h={h} />
          </div>
          <Recent rows={h.recent} />
        </>
      )}

      <p className="mono text-[10px] text-[var(--haze)] mt-6 leading-relaxed">
        Priced off the rates published on {h.rates.priced_on}; a model outside that
        table is reported as unpriced rather than as free. Ledger at{" "}
        <span className="text-[var(--bright)]">{h.ledger_path}</span>.
        {spent && (
          <>
            {" "}
            <button className="underline underline-offset-2 hover:text-[var(--bright)]"
                    onClick={() => { clearSpend().then(load).catch(() => {}); }}>
              Forget this history
            </button>
            .
          </>
        )}
      </p>
    </section>
  );
}

/** The empty state, which is a sentence rather than a grid of zeroes. */
function Nothing({ provider, days }: { provider: string; days: number }) {
  return (
    <div className="py-10 text-center">
      <div className="mono text-[13px] text-[var(--bright)]">nothing spent in {days} days</div>
      <p className="text-[12px] text-[var(--haze)] mt-2 max-w-[52ch] mx-auto leading-relaxed">
        {provider === "none"
          ? "No provider is configured, so nothing here can cost anything. Every finding, byte count and access path in the console is derived from metadata with no model involved."
          : "The provider is configured but has not been asked for anything yet. Summarise a table or ask a question in English, and the call will land here with what it cost."}
      </p>
    </div>
  );
}

/* ---------------------------------------------------------------------- stats */

function Stat({ label, value, tone, note }: {
  label: string; value: string; tone?: string; note: string;
}) {
  return (
    <div className="px-4 py-3.5 rounded-sm border" style={{ borderColor: "var(--rule)" }}>
      <div className="eyebrow mb-1.5">{label}</div>
      <div className="mono font-bold leading-none" style={{ fontSize: 26, color: tone ?? "var(--bright)" }}>
        {value}
      </div>
      <div className="mono text-[10px] text-[var(--haze)] mt-2 leading-relaxed">{note}</div>
    </div>
  );
}

function Stats({ h }: { h: SpendHistory }) {
  const t = h.totals;
  const perCall = t.calls > 0 ? t.cost_usd / t.calls : 0;
  const hitRate = t.calls + t.cache_hits > 0
    ? Math.round((t.cache_hits / (t.calls + t.cache_hits)) * 100) : 0;
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
      <Stat label={`spent · ${h.window_days}d`} value={usd(t.cost_usd)} tone="var(--index)"
            note={t.unpriced_calls > 0
              ? `${usd(perCall)} per call · ${t.unpriced_calls} on an unpriced model, not counted here`
              : `${usd(perCall)} per call on average`} />
      <Stat label="calls" value={String(t.calls)}
            note={`${tok(t.input_tokens)} in · ${tok(t.output_tokens)} out · ${ms(t.avg_ms)} typical`} />
      <Stat label="saved by cache" value={usd(t.avoided_usd)} tone="var(--index)"
            note={t.cache_hits > 0
              ? `${t.cache_hits} answer${t.cache_hits === 1 ? "" : "s"} came off disk — ${hitRate}% of asks`
              : "no answer has been served from the cache yet"} />
      <Stat label="this process" value={usd(h.session.cost_usd)}
            note={`${h.session.calls} call${h.session.calls === 1 ? "" : "s"} since the server started ${
              Math.max(1, Math.round(h.session.seconds / 60))} min ago`} />
    </div>
  );
}

/* -------------------------------------------------------------------- ceiling */

/** How much of the configured cap is gone.
 *
 *  A cap is the one number on this panel with a consequence attached — reaching it
 *  refuses the next call — so it is drawn rather than printed. It turns coral at
 *  80%, which is early enough to be a warning rather than a post-mortem. */
function Ceiling({ spent, cap }: { spent: number; cap: number }) {
  const pct = cap > 0 ? Math.min(1, spent / cap) : 0;
  const hot = pct >= 0.8;
  return (
    <div className="mb-7">
      <div className="flex items-baseline justify-between mb-1.5">
        <span className="eyebrow">against the ceiling</span>
        <span className="mono text-[11px] text-[var(--haze)]">
          <span style={{ color: hot ? "var(--video)" : "var(--index)" }}>{usd(spent)}</span>
          {" "}of {usd(cap)} · {Math.round(pct * 100)}%
        </span>
      </div>
      <div className="h-2 rounded-sm overflow-hidden" style={{ background: "var(--rail)", border: "1px solid var(--rule)" }}>
        <div className="h-full transition-all duration-700 ease-out"
             style={{ width: `${Math.max(pct * 100, spent > 0 ? 1.5 : 0)}%`,
                      background: hot ? "var(--video)" : "var(--index)" }} />
      </div>
      <p className="mono text-[10px] text-[var(--haze)] mt-1.5">
        {pct >= 1
          ? "reached — the next call is refused before it is made, not after"
          : "the check runs before a call, so the cap is a limit rather than a receipt"}
      </p>
    </div>
  );
}

/* ---------------------------------------------------------------------- daily */

type Metric = "cost" | "tokens" | "calls";

const METRICS: { id: Metric; label: string }[] = [
  { id: "cost", label: "dollars" },
  { id: "tokens", label: "tokens" },
  { id: "calls", label: "calls" },
];

const valueOf = (b: SpendBucket, m: Metric): number =>
  m === "cost" ? b.cost_usd
    : m === "tokens" ? b.input_tokens + b.output_tokens
      : b.calls;

const fmtOf = (n: number, m: Metric): string =>
  m === "cost" ? usd(n) : m === "tokens" ? tok(n) : String(n);

/** A day per bar, stacked by task, with the cumulative total drawn over it.
 *
 *  Every day in the window is plotted, including the ones nothing happened on: a
 *  chart that skipped them would draw a busy week and a quiet week identically. The
 *  bars answer "what did Tuesday cost and on what"; the line answers "where is this
 *  going", which is the question a ceiling is set in response to. */
function Daily({ h, cap }: { h: SpendHistory; cap: number | null }) {
  const [metric, setMetric] = useState<Metric>("cost");
  const rows = h.daily;

  const W = 720, H = 190, L = 46, R = 12, T = 12, B = 22;
  const iw = W - L - R, ih = H - T - B;

  const tasks = h.by_task.map((b) => b.task ?? "other");
  const peak = Math.max(...rows.map((d) => valueOf(d, metric)), 0);
  const scale = peak > 0 ? peak : 1;

  const bw = iw / rows.length;
  const y = (v: number) => T + ih - (v / scale) * ih;

  // A prefix sum written without a running variable: the window is ninety points at
  // the very most, so the cost of rebuilding the array is not worth a mutation.
  const cum = rows.reduce<number[]>(
    (acc, d) => [...acc, (acc[acc.length - 1] ?? 0) + valueOf(d, metric)], []);
  const cumMax = Math.max(cum[cum.length - 1] ?? 0, 1);
  const line = cum
    .map((v, i) => `${L + bw * (i + 0.5)},${T + ih - (v / cumMax) * ih}`)
    .join(" ");

  return (
    <div>
      <div className="flex items-baseline justify-between gap-3 flex-wrap mb-2">
        <span className="eyebrow">by day</span>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2.5">
            {tasks.map((k) => (
              <span key={k} className="mono text-[10px] flex items-center gap-1.5"
                    style={{ color: "var(--haze)" }} title={TASK_WHAT[k] ?? ""}>
                <span style={{ width: 8, height: 8, background: color(k), borderRadius: 1 }} />
                {k}
              </span>
            ))}
          </div>
          <div className="flex items-center gap-1">
            {METRICS.map((m) => (
              <button key={m.id}
                      className="mono text-[10px] px-1.5 py-0.5 rounded-sm"
                      style={m.id === metric
                        ? { color: "var(--index)", border: "1px solid rgb(var(--index-rgb) / 0.45)" }
                        : { color: "var(--haze)", border: "1px solid transparent" }}
                      onClick={() => setMetric(m.id)}>
                {m.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: "auto" }}
           role="img" aria-label={`${metric} per day over ${h.window_days} days`}>
        {/* Three gridlines and their values. Any more and the grid competes with
            the data it is there to make readable. */}
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line x1={L} x2={W - R} y1={y(scale * f)} y2={y(scale * f)}
                  stroke="var(--hairline)" strokeWidth={1} />
            <text x={L - 6} y={y(scale * f) + 3} textAnchor="end"
                  className="mono" fontSize={9} fill="var(--haze)">
              {fmtOf(scale * f, metric)}
            </text>
          </g>
        ))}

        {/* The ceiling, where one is set and the chart is in dollars. Drawn only if
            it would land on the chart — a line pinned to the top edge because the
            cap is ten times the busiest day says nothing true about either. */}
        {metric === "cost" && cap !== null && cap <= scale && cap > 0 && (
          <g>
            <line x1={L} x2={W - R} y1={y(cap)} y2={y(cap)}
                  stroke="var(--video)" strokeWidth={1} strokeDasharray="3 3" opacity={0.8} />
            <text x={W - R} y={y(cap) - 4} textAnchor="end"
                  className="mono" fontSize={9} fill="var(--video)">ceiling</text>
          </g>
        )}

        {rows.map((d, i) => {
          const total = valueOf(d, metric);
          const x = L + i * bw;
          let top = T + ih;
          const parts = tasks
            .map((k) => ({ k, v: d.tasks?.[k] ? valueOf(d.tasks[k], metric) : 0 }))
            .filter((p) => p.v > 0);
          return (
            <g key={d.day}>
              {parts.map((p) => {
                const px = Math.max((p.v / scale) * ih, 1);
                top -= px;
                return (
                  <rect key={p.k} x={x + bw * 0.16} y={top}
                        width={Math.max(bw * 0.68, 1)} height={px} fill={color(p.k)} rx={1} />
                );
              })}
              {/* One target per day, so the tooltip is reachable even on a day with
                  nothing in it. */}
              <rect x={x} y={T} width={bw} height={ih} fill="transparent">
                <title>{`${d.day} · ${fmtOf(total, metric)}${
                  parts.length ? ` — ${parts.map((p) => `${p.k} ${fmtOf(p.v, metric)}`).join(", ")}` : ""
                }${d.cache_hits ? ` · ${d.cache_hits} from cache` : ""}`}</title>
              </rect>
            </g>
          );
        })}

        {/* Cumulative, on its own scale — the shape is the claim, not the height. */}
        <polyline points={line} fill="none" stroke="var(--bright)" strokeWidth={1.2}
                  opacity={0.42} strokeLinejoin="round" />

        <text x={L} y={H - 6} className="mono" fontSize={9} fill="var(--haze)">
          {rows[0]?.day}
        </text>
        <text x={W - R} y={H - 6} textAnchor="end" className="mono" fontSize={9} fill="var(--haze)">
          {rows[rows.length - 1]?.day}
        </text>
      </svg>
      <p className="mono text-[10px] text-[var(--haze)] mt-1 leading-relaxed">
        Bars are one day, stacked by task. The faint line is the running total across
        the window, scaled to its own end — it is there for the shape, not the height.
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ task split */

/** Where the money went, by the thing that asked for it.
 *
 *  A ring rather than a stack of bars because there are three or four of these and
 *  the interesting fact is the proportion — that translation is most of the calls
 *  and almost none of the bill is the sentence this chart exists to make. */
function Mix({ by, total, days }: { by: SpendBucket[]; total: number; days: number }) {
  const priced = by.filter((b) => b.cost_usd > 0);
  const R = 54, SW = 15, C = 70;
  const circ = 2 * Math.PI * R;

  const arcs = priced.reduce<{ b: SpendBucket; frac: number; offset: number }[]>(
    (segs, b) => {
      const prev = segs[segs.length - 1];
      const frac = total > 0 ? b.cost_usd / total : 0;
      return [...segs, { b, frac, offset: prev ? prev.offset + prev.frac : 0 }];
    }, []);

  return (
    <div>
      <div className="eyebrow mb-2">by task</div>
      {priced.length === 0 ? (
        <p className="mono text-[11px] text-[var(--haze)] leading-relaxed">
          Nothing priced yet — every call so far ran on a model with no published rate.
        </p>
      ) : (
        <div className="flex items-center gap-5">
          <svg viewBox="0 0 140 140" width={140} height={140} className="shrink-0" role="img"
               aria-label="share of spend by task">
            <circle cx={C} cy={C} r={R} fill="none" stroke="var(--hairline)" strokeWidth={SW} />
            {arcs.map(({ b, frac, offset }) => (
              <circle key={b.task} cx={C} cy={C} r={R} fill="none"
                      stroke={color(b.task ?? "other")} strokeWidth={SW}
                      strokeDasharray={`${frac * circ} ${circ}`}
                      strokeDashoffset={-offset * circ}
                      transform={`rotate(-90 ${C} ${C})`}>
                <title>{`${b.task} · ${usd(b.cost_usd)} · ${Math.round(frac * 100)}%`}</title>
              </circle>
            ))}
            <text x={C} y={C - 2} textAnchor="middle" className="mono" fontSize={17}
                  fontWeight={700} fill="var(--bright)">{usd(total)}</text>
            <text x={C} y={C + 13} textAnchor="middle" className="mono" fontSize={9}
                  fill="var(--haze)">{days} days</text>
          </svg>
          <div className="min-w-0">
            {by.map((b) => (
              <div key={b.task} className="mb-2.5 last:mb-0">
                <div className="mono text-[11.5px] flex items-center gap-2">
                  <span style={{ width: 8, height: 8, background: color(b.task ?? "other"), borderRadius: 1 }} />
                  <span style={{ color: "var(--bright)" }}>{b.task}</span>
                  <span style={{ color: "var(--index)" }}>{usd(b.cost_usd)}</span>
                </div>
                <div className="mono text-[10px] text-[var(--haze)] ml-4">
                  {b.calls} call{b.calls === 1 ? "" : "s"}
                  {b.calls > 0 && <> · {usd(b.cost_usd / b.calls)} each · {ms(b.avg_ms)}</>}
                  {b.cache_hits > 0 && <> · {b.cache_hits} free from cache</>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* --------------------------------------------------------------------- models */

/** What each model was asked for and what it charged for it.
 *
 *  The published rate is carried beside the observed spend deliberately: a rate
 *  without usage is a price list, and usage without the rate is a number you cannot
 *  check. Together they are the arithmetic, and anyone can redo it. */
function Models({ h }: { h: SpendHistory }) {
  const rate = (id: string) => h.rates.models.find((m) => m.id === id);
  const peak = Math.max(...h.by_model.map((b) => b.cost_usd), 0.000001);

  return (
    <div className="min-w-0">
      <div className="eyebrow mb-2">by model</div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <Th>model</Th>
              <Th right>calls</Th>
              <Th right>in / out</Th>
              <Th right>$/Mtok</Th>
              <Th right>spent</Th>
            </tr>
          </thead>
          <tbody>
            {h.by_model.map((b) => {
              const r = rate(b.model ?? "");
              return (
                <tr key={b.model} style={{ borderBottom: "1px solid var(--hairline)" }}>
                  <Td>
                    <div style={{ color: "var(--bright)" }}>{b.model}</div>
                    <div className="text-[10px] text-[var(--haze)]">{b.provider || "—"}</div>
                  </Td>
                  <Td right>
                    {b.calls}
                    {b.cache_hits > 0 && (
                      <div className="text-[10px] text-[var(--haze)]">+{b.cache_hits} cached</div>
                    )}
                  </Td>
                  <Td right dim>{tok(b.input_tokens)} / {tok(b.output_tokens)}</Td>
                  <Td right dim>
                    {r?.input_usd_per_mtok != null
                      ? `${r.input_usd_per_mtok} / ${r.output_usd_per_mtok}`
                      : "unpriced"}
                  </Td>
                  <Td right>
                    <span style={{ color: b.unpriced_calls === b.calls ? "var(--haze)" : "var(--index)" }}>
                      {b.unpriced_calls === b.calls ? "—" : usd(b.cost_usd)}
                    </span>
                    <div className="mt-1 h-[3px] rounded-sm" style={{ background: "var(--hairline)" }}>
                      <div className="h-full rounded-sm"
                           style={{ width: `${Math.max((b.cost_usd / peak) * 100, 0)}%`,
                                    background: "var(--index)" }} />
                    </div>
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {h.totals.unpriced_calls > 0 && (
        <p className="mono text-[10px] text-[var(--haze)] mt-2 leading-relaxed">
          An unpriced model is free if it is running on this machine and unknown if it
          is not. Neither is $0.00, so neither is written as one.
        </p>
      )}
    </div>
  );
}

/* --------------------------------------------------------------------- recent */

function Recent({ rows }: { rows: SpendEvent[] }) {
  const [open, setOpen] = useState(false);
  if (rows.length === 0) return null;
  const shown = open ? rows : rows.slice(0, 8);

  return (
    <div className="mt-8">
      <div className="flex items-baseline justify-between mb-2">
        <span className="eyebrow">recent calls</span>
        {rows.length > 8 && (
          <button className="mono text-[10px] text-[var(--haze)] hover:text-[var(--bright)]"
                  onClick={() => setOpen((o) => !o)}>
            {open ? "show fewer" : `all ${rows.length}`}
          </button>
        )}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <Th>when</Th>
              <Th>task</Th>
              <Th>model</Th>
              <Th right>in / out</Th>
              <Th right>took</Th>
              <Th right>cost</Th>
            </tr>
          </thead>
          <tbody>
            {shown.map((e, i) => (
              <tr key={`${e.ts}-${i}`} style={{ borderBottom: "1px solid var(--hairline)" }}>
                <Td dim>{clock(e.ts)}</Td>
                <Td>
                  <span className="flex items-center gap-1.5">
                    <span style={{ width: 7, height: 7, background: color(e.task), borderRadius: 1 }} />
                    {e.task}
                  </span>
                </Td>
                <Td dim>{e.model || "—"}</Td>
                <Td right dim>
                  {e.cached ? "—" : `${tok(e.input_tokens)} / ${tok(e.output_tokens)}`}
                </Td>
                <Td right dim>{e.cached ? "—" : ms(e.ms)}</Td>
                <Td right>
                  {e.cached ? (
                    <span style={{ color: "var(--index)" }} title="served from the cache">
                      cached{e.avoided_usd ? ` · saved ${usd(e.avoided_usd)}` : ""}
                    </span>
                  ) : e.cost_usd === null ? (
                    <span className="text-[var(--haze)]">unpriced</span>
                  ) : (
                    <span style={{ color: e.cost_usd > 0 ? "var(--index)" : "var(--haze)" }}>
                      {e.cost_usd === 0 ? "free · local" : usd(e.cost_usd)}
                    </span>
                  )}
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
