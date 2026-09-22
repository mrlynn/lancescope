"use client";

/** What a vector index gives up for its speed, as two curves.
 *
 *  Recall against the bytes one search reads. The first series is `nprobes` — a
 *  point per power of two, up to every partition — and answers "is it missing
 *  neighbours because it does not look in enough places". The second is
 *  `refine_factor` at the default `nprobes`, and answers the other half: "is it
 *  missing them because the compressed distances rank them out". It starts from the
 *  default point, because that is the search it refines.
 *
 *  The x axis is logarithmic because the settings double, and a doubling is the step
 *  somebody tuning this actually takes. The exact scan sits off the scale at the top
 *  right as the thing every point is trading against: all of the neighbours, at the
 *  price of the whole column.
 *
 *  Two series, so identity is never colour alone: circles on a solid line against
 *  squares on a dashed one, a legend, and every point labelled with its setting. The
 *  two hues are `--series-1` and `--series-2`, validated as a pair in both themes.
 *
 *  Rendered from the finding's evidence and nothing else, so the chart and the claim
 *  above it cannot describe different measurements. */

import { useEffect, useRef, useState } from "react";
import { fmtBytes } from "@/app/lib/api";

export type RecallPoint = {
  nprobes?: number | null;
  refine_factor?: number;
  recall: number;
  read_bytes: number;
  ms: number;
};

const H = 220;
const L = 40, R = 76, T = 22, B = 36;
const TARGET = 0.95;
const PROBE = "var(--series-1)";
const REFINE = "var(--series-2)";

function bytes(n: number): string {
  const b = fmtBytes(Math.round(n));
  return `${b.value} ${b.unit}`;
}

function pct(r: number): string {
  return `${(r * 100).toFixed(r === 1 ? 0 : 1)}%`;
}

function setting(p: RecallPoint): string {
  if (p.refine_factor !== undefined) return `refine_factor ${p.refine_factor}`;
  return p.nprobes === null || p.nprobes === undefined ? "default setting"
    : `nprobes ${p.nprobes}`;
}

export function isRecallCurve(v: unknown): v is RecallPoint[] {
  return Array.isArray(v) && v.length > 0 && v.every(
    (p) => p && typeof p === "object" && "recall" in p && "read_bytes" in p);
}

type Label = { px: number; py: number; from: string; to: string };

/** Settings that land on one spot share a label. Past the point where every
 *  partition worth reading has been read, doubling `nprobes` changes nothing — and
 *  that plateau reads better as "8–64" than as five numbers on one dot. */
function merged(points: { px: number; py: number; name: string }[]): Label[] {
  return points.reduce<Label[]>((acc, p) => {
    const last = acc[acc.length - 1];
    if (last && Math.abs(last.px - p.px) < 12 && Math.abs(last.py - p.py) < 8) {
      // Anchored under the group's lowest mark, or it sits on one of the others.
      last.to = p.name;
      last.py = Math.max(last.py, p.py);
      return acc;
    }
    return [...acc, { px: p.px, py: p.py, from: p.name, to: p.name }];
  }, []);
}

export function RecallCurve({ curve, refineCurve = [], refineSkipped = "", exactBytes,
                              partitions }: {
  curve: RecallPoint[];
  refineCurve?: RecallPoint[];
  refineSkipped?: string;
  exactBytes: number;
  partitions: number;
}) {
  const [hover, setHover] = useState<RecallPoint | null>(null);
  // Drawn at the width it is shown at rather than scaled into it, so a 9px label is
  // 9px. A finding card is narrow, and a viewBox shrunk to fit it takes the text
  // down to something nobody can read.
  const box = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(480);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(260, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const swept = curve.filter((p) => p.nprobes !== null && p.nprobes !== undefined);
  const fallback = curve.find((p) => p.nprobes === null) ?? null;

  // The axis spans the settings, not the exact scan. The exact scan is usually two
  // or three orders of magnitude further right, and giving it a place on the scale
  // squeezes every setting into one corner — so it sits at the edge, with its size
  // written on it instead.
  const all = [...curve, ...refineCurve].map((p) => Math.max(p.read_bytes, 1));
  const lo = Math.log10(Math.min(...all) / 1.25);
  const hi = Math.log10(Math.max(...all) * 1.25);
  const iw = W - L - R, ih = H - T - B;
  const x = (b: number) => L + ((Math.log10(Math.max(b, 1)) - lo) / (hi - lo)) * iw;
  // From the nearest ten percent below the lowest point, not from zero. This is a
  // line, not a bar — its height is not the claim — and a zero baseline under a curve
  // that lives between 60% and 100% spends half the chart on space nothing reaches
  // while stacking every refined point on top of the target line.
  // The 0.08 keeps the lowest point, and the label under it, clear of the axis.
  const floor = Math.max(0, Math.min(0.9, Math.floor(
    (Math.min(...[...curve, ...refineCurve].map((p) => p.recall)) - 0.08) * 10) / 10));
  const y = (r: number) => T + ((1 - r) / (1 - floor)) * ih;
  const grid = [floor, (floor + 1) / 2, 1];

  // A tick at every power of ten in range. On a table where the index reads
  // kilobytes and the exact scan gigabytes, those are the landmarks worth having.
  const ticks: number[] = [];
  for (let e = Math.ceil(lo); e <= Math.floor(hi); e++) ticks.push(10 ** e);
  // A narrow span can hold no power of ten at all; then the ends are the landmarks.
  if (ticks.length < 2) ticks.splice(0, ticks.length, 10 ** (lo + 0.1), 10 ** (hi - 0.1));

  const pt = (p: RecallPoint) => `${x(p.read_bytes)},${y(p.recall)}`;
  const probeLine = swept.map(pt).join(" ");
  // From the default, because refining is applied to the default search.
  const refineLine = [...(fallback ? [fallback] : []), ...refineCurve].map(pt).join(" ");

  const probeLabels = merged(swept.map((p) => ({
    px: x(p.read_bytes), py: y(p.recall), name: String(p.nprobes) })));
  const refineLabels = merged(refineCurve.map((p) => ({
    px: x(p.read_bytes), py: y(p.recall), name: `×${p.refine_factor}` })));

  const targets = [...curve, ...refineCurve];

  return (
    <div className="mt-3" ref={box}>
      {refineCurve.length > 0 && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 mb-1.5 mono text-[10px]"
             style={{ color: "var(--haze)" }}>
          <span className="flex items-center gap-1.5">
            <svg width={18} height={8} aria-hidden>
              <line x1={0} x2={18} y1={4} y2={4} stroke={PROBE} strokeWidth={2} />
              <circle cx={9} cy={4} r={3} fill={PROBE} />
            </svg>
            nprobes
          </span>
          <span className="flex items-center gap-1.5">
            <svg width={18} height={8} aria-hidden>
              <line x1={0} x2={18} y1={4} y2={4} stroke={REFINE} strokeWidth={2}
                    strokeDasharray="4 3" />
              <rect x={6} y={1} width={6} height={6} rx={1} fill={REFINE} />
            </svg>
            refine_factor, at the default nprobes
          </span>
        </div>
      )}

      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: "auto" }}
             role="img"
             aria-label={`Recall against bytes per search for ${swept.length} nprobes settings`
                         + (refineCurve.length
                            ? ` and ${refineCurve.length} refine_factor settings` : "")}
             onMouseLeave={() => setHover(null)}>
          {grid.map((r) => (
            <g key={r}>
              <line x1={L} x2={W - R} y1={y(r)} y2={y(r)}
                    stroke="var(--hairline)" strokeWidth={1} />
              <text x={L - 6} y={y(r) + 3} textAnchor="end" className="mono"
                    fontSize={9} fill="var(--haze)">{pct(r)}</text>
            </g>
          ))}
          {ticks.map((t) => (
            <text key={t} x={x(t)} y={H - B + 14} textAnchor="middle" className="mono"
                  fontSize={9} fill="var(--haze)">{bytes(t)}</text>
          ))}
          <text x={L + iw / 2} y={H - 6} textAnchor="middle" className="mono"
                fontSize={9} fill="var(--haze)">bytes read per search (log)</text>

          <line x1={L} x2={W - R} y1={y(TARGET)} y2={y(TARGET)}
                stroke="var(--rule)" strokeWidth={1} strokeDasharray="3 3" />
          {/* Left and under the line: the cheap settings that sit there are below
              the target by definition, and the ones above it crowd the right. */}
          <text x={L + 4} y={y(TARGET) + 11} className="mono"
                fontSize={9} fill="var(--haze)">{pct(TARGET)} target</text>

          {/* The exact scan: every neighbour, for the whole column — off the scale. */}
          <rect x={W - R + 10} y={y(1) - 4} width={8} height={8} rx={1}
                fill="var(--haze)" stroke="var(--ink-2)" strokeWidth={2} />
          <text x={W - R + 10} y={y(1) + 16} className="mono" fontSize={9}
                fill="var(--haze)">exact scan</text>
          <text x={W - R + 10} y={y(1) + 28} className="mono" fontSize={9}
                fill="var(--haze)">{bytes(exactBytes)}</text>

          <polyline points={probeLine} fill="none" stroke={PROBE} strokeWidth={2}
                    strokeLinejoin="round" strokeLinecap="round" />
          {refineCurve.length > 0 && (
            <polyline points={refineLine} fill="none" stroke={REFINE} strokeWidth={2}
                      strokeDasharray="4 3" strokeLinejoin="round" />
          )}

          {swept.map((p) => (
            <circle key={`p-${p.nprobes}`} cx={x(p.read_bytes)} cy={y(p.recall)} r={4}
                    fill={PROBE} stroke="var(--ink-2)" strokeWidth={2} />
          ))}
          {refineCurve.map((p) => (
            <rect key={`r-${p.refine_factor}`} x={x(p.read_bytes) - 4}
                  y={y(p.recall) - 4} width={8} height={8} rx={1}
                  fill={REFINE} stroke="var(--ink-2)" strokeWidth={2} />
          ))}
          {/* A single setting is labelled under its mark. A plateau's label is wider
              than the gap to the setting before it, so it goes beside the group. */}
          {probeLabels.map((g) => (g.from === g.to ? (
            <text key={`pl-${g.from}`} x={g.px} y={g.py + 19} textAnchor="middle"
                  className="mono" fontSize={9} fill="var(--haze)">{g.from}</text>
          ) : (
            <text key={`pl-${g.from}`} x={g.px + 12} y={g.py + 4}
                  className="mono" fontSize={9} fill="var(--haze)">
              {`${g.from}–${g.to}`}
            </text>
          )))}
          {/* Above the squares, so they never sit on the nprobes labels below. */}
          {refineLabels.map((g) => (
            <text key={`rl-${g.from}`} x={g.px} y={g.py - 9} textAnchor="middle"
                  className="mono" fontSize={9} fill="var(--haze)">
              {g.from === g.to ? g.from : `${g.from}–${g.to.replace("×", "")}`}
            </text>
          ))}

          {/* What a search that names no setting gets — the point the claim is about. */}
          {fallback && (
            <g>
              <circle cx={x(fallback.read_bytes)} cy={y(fallback.recall)} r={8}
                      fill="none" stroke="var(--bright)" strokeWidth={1.5} />
              <text x={x(fallback.read_bytes)} y={y(fallback.recall) - 13}
                    textAnchor="middle" className="mono" fontSize={9}
                    fill="var(--bright)">default</text>
            </g>
          )}

          {/* Hit targets well past the mark, so a point is easy to land on. */}
          {targets.map((p, i) => (
            <circle key={`hit-${i}`} cx={x(p.read_bytes)} cy={y(p.recall)} r={12}
                    fill="transparent" onMouseEnter={() => setHover(p)}
                    onFocus={() => setHover(p)} onBlur={() => setHover(null)}
                    tabIndex={0}
                    aria-label={`${setting(p)} — ${pct(p.recall)} recall, ${bytes(p.read_bytes)} per search`} />
          ))}
        </svg>

        {hover && (
          <div className="absolute pointer-events-none mono text-[10px] leading-relaxed
                          px-2.5 py-1.5 rounded-sm"
               style={{
                 left: `${(x(hover.read_bytes) / W) * 100}%`,
                 top: `${(y(hover.recall) / H) * 100}%`,
                 transform: "translate(-50%, calc(-100% - 12px))",
                 background: "var(--ink-2)", border: "1px solid var(--rule)",
                 color: "var(--body)", whiteSpace: "nowrap",
               }}>
            <div style={{ color: "var(--bright)" }}>{setting(hover)}</div>
            {hover.refine_factor !== undefined && <div>at the default nprobes</div>}
            <div>{pct(hover.recall)} of true neighbours</div>
            <div>{bytes(hover.read_bytes)} · {hover.ms} ms a search</div>
          </div>
        )}
      </div>

      {refineSkipped && (
        <p className="mono text-[10px] text-[var(--haze)] mt-1 leading-relaxed">
          No refine_factor series: {refineSkipped}
        </p>
      )}

      <details className="mt-1">
        <summary className="mono text-[10px] text-[var(--haze)] cursor-pointer">
          as a table · {partitions.toLocaleString()} partitions
        </summary>
        <table className="mono text-[11px] mt-2">
          <thead>
            <tr className="text-[var(--haze)]">
              <th className="text-left pr-5 font-normal">setting</th>
              <th className="text-right pr-5 font-normal">recall</th>
              <th className="text-right pr-5 font-normal">per search</th>
              <th className="text-right font-normal">ms</th>
            </tr>
          </thead>
          <tbody className="text-[var(--bright)]">
            {targets.map((p) => (
              <tr key={setting(p)}>
                <td className="pr-5">{setting(p)}</td>
                <td className="text-right pr-5">{pct(p.recall)}</td>
                <td className="text-right pr-5">{bytes(p.read_bytes)}</td>
                <td className="text-right">{p.ms}</td>
              </tr>
            ))}
            <tr className="text-[var(--haze)]">
              <td className="pr-5">exact scan</td>
              <td className="text-right pr-5">100%</td>
              <td className="text-right pr-5">{bytes(exactBytes)}</td>
              <td />
            </tr>
          </tbody>
        </table>
      </details>
    </div>
  );
}
