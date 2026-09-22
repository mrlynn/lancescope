"use client";

/** What a vector index gives up for its speed, as one curve.
 *
 *  Recall against the bytes one search reads, a point per `nprobes` setting. The
 *  x axis is logarithmic because the settings double, and a doubling is the step
 *  somebody tuning this actually takes. The exact scan sits off the scale at the top
 *  right as the thing every point is trading against: all of the neighbours, at the
 *  price of the whole column. One series, so no legend — the settings are labelled where they are.
 *
 *  Rendered from the finding's `curve` evidence and nothing else, so the chart and
 *  the claim above it cannot describe different measurements. */

import { useEffect, useRef, useState } from "react";
import { fmtBytes } from "@/app/lib/api";

export type RecallPoint = {
  nprobes: number | null;
  recall: number;
  read_bytes: number;
  ms: number;
};

const H = 210;
const L = 40, R = 76, T = 22, B = 36;
const TARGET = 0.95;

function bytes(n: number): string {
  const b = fmtBytes(Math.round(n));
  return `${b.value} ${b.unit}`;
}

function pct(r: number): string {
  return `${(r * 100).toFixed(r === 1 ? 0 : 1)}%`;
}

export function isRecallCurve(v: unknown): v is RecallPoint[] {
  return Array.isArray(v) && v.length > 0 && v.every(
    (p) => p && typeof p === "object" && "recall" in p && "read_bytes" in p);
}

export function RecallCurve({ curve, exactBytes, partitions }: {
  curve: RecallPoint[];
  exactBytes: number;
  partitions: number;
}) {
  const [hover, setHover] = useState<number | null>(null);
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
  const swept = curve.filter((p) => p.nprobes !== null);
  const fallback = curve.find((p) => p.nprobes === null) ?? null;

  // The axis spans the settings, not the exact scan. The exact scan is usually two
  // or three orders of magnitude further right, and giving it a place on the scale
  // squeezes every setting into one corner — so it sits at the edge, with its size
  // written on it instead.
  const all = curve.map((p) => Math.max(p.read_bytes, 1));
  const lo = Math.log10(Math.min(...all) / 1.25);
  const hi = Math.log10(Math.max(...all) * 1.25);
  const iw = W - L - R, ih = H - T - B;
  const x = (b: number) => L + ((Math.log10(Math.max(b, 1)) - lo) / (hi - lo)) * iw;
  const y = (r: number) => T + (1 - r) * ih;

  // A tick at every power of ten in range. On a table where the index reads
  // kilobytes and the exact scan gigabytes, those are the landmarks worth having.
  const ticks: number[] = [];
  for (let e = Math.ceil(lo); e <= Math.floor(hi); e++) ticks.push(10 ** e);
  // A narrow span can hold no power of ten at all; then the ends are the landmarks.
  if (ticks.length < 2) ticks.splice(0, ticks.length, 10 ** (lo + 0.1), 10 ** (hi - 0.1));

  const line = swept.map((p) => `${x(p.read_bytes)},${y(p.recall)}`).join(" ");

  // Settings that land on the same spot share one label. Past the point where every
  // partition worth reading has been read, doubling `nprobes` changes nothing — and
  // that plateau is worth saying as "8–64" rather than as five numbers on one dot.
  const labels = swept.reduce<{ px: number; py: number; from: number; to: number }[]>(
    (acc, p) => {
      const px = x(p.read_bytes), py = y(p.recall);
      const last = acc[acc.length - 1];
      if (last && Math.abs(last.px - px) < 12 && Math.abs(last.py - py) < 8) {
        last.to = p.nprobes as number;
        return acc;
      }
      return [...acc, { px, py, from: p.nprobes as number, to: p.nprobes as number }];
    }, []);
  const shown = hover === null ? null : curve[hover];

  return (
    <div className="mt-3" ref={box}>
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: "auto" }}
             role="img"
             aria-label={`Recall against bytes per search for ${swept.length} nprobes settings`}
             onMouseLeave={() => setHover(null)}>
          {[0, 0.5, 1].map((r) => (
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
          <text x={W - R - 2} y={y(TARGET) - 4} textAnchor="end" className="mono"
                fontSize={9} fill="var(--haze)">{pct(TARGET)} target</text>

          {/* The exact scan: every neighbour, for the whole column — off the scale. */}
          <rect x={W - R + 10} y={y(1) - 4} width={8} height={8} rx={1}
                fill="var(--haze)" stroke="var(--ink-2)" strokeWidth={2} />
          <text x={W - R + 10} y={y(1) + 16} className="mono" fontSize={9}
                fill="var(--haze)">exact scan</text>
          <text x={W - R + 10} y={y(1) + 28} className="mono" fontSize={9}
                fill="var(--haze)">{bytes(exactBytes)}</text>

          <polyline points={line} fill="none" stroke="var(--index)" strokeWidth={2}
                    strokeLinejoin="round" strokeLinecap="round" />

          {swept.map((p) => (
            <circle key={p.nprobes} cx={x(p.read_bytes)} cy={y(p.recall)} r={4}
                    fill="var(--index)" stroke="var(--ink-2)" strokeWidth={2} />
          ))}
          {labels.map((g) => (
            <text key={g.from} x={g.px} y={g.py + 16} textAnchor="middle"
                  className="mono" fontSize={9} fill="var(--haze)">
              {g.from === g.to ? g.from : `${g.from}–${g.to}`}
            </text>
          ))}

          {/* What a search that names no setting gets — the point the claim is about. */}
          {fallback && (
            <g>
              <circle cx={x(fallback.read_bytes)} cy={y(fallback.recall)} r={8}
                      fill="none" stroke="var(--bright)" strokeWidth={1.5} />
              <text x={x(fallback.read_bytes)} y={y(fallback.recall) - 12}
                    textAnchor="middle" className="mono" fontSize={9}
                    fill="var(--bright)">default</text>
            </g>
          )}

          {/* Hit targets well past the mark, so a point is easy to land on. */}
          {curve.map((p, i) => (
            <circle key={`hit-${i}`} cx={x(p.read_bytes)} cy={y(p.recall)} r={12}
                    fill="transparent" onMouseEnter={() => setHover(i)}
                    onFocus={() => setHover(i)} onBlur={() => setHover(null)}
                    tabIndex={0}
                    aria-label={`${p.nprobes ?? "default"} — ${pct(p.recall)} recall, ${bytes(p.read_bytes)} per search`} />
          ))}
        </svg>

        {shown && (
          <div className="absolute pointer-events-none mono text-[10px] leading-relaxed
                          px-2.5 py-1.5 rounded-sm"
               style={{
                 left: `${(x(shown.read_bytes) / W) * 100}%`,
                 top: `${(y(shown.recall) / H) * 100}%`,
                 transform: "translate(-50%, calc(-100% - 12px))",
                 background: "var(--ink-2)", border: "1px solid var(--rule)",
                 color: "var(--body)", whiteSpace: "nowrap",
               }}>
            <div style={{ color: "var(--bright)" }}>
              {shown.nprobes === null ? "default setting" : `nprobes ${shown.nprobes}`}
            </div>
            <div>{pct(shown.recall)} of true neighbours</div>
            <div>{bytes(shown.read_bytes)} · {shown.ms} ms a search</div>
          </div>
        )}
      </div>

      <details className="mt-1">
        <summary className="mono text-[10px] text-[var(--haze)] cursor-pointer">
          as a table · {partitions.toLocaleString()} partitions
        </summary>
        <table className="mono text-[11px] mt-2">
          <thead>
            <tr className="text-[var(--haze)]">
              <th className="text-left pr-5 font-normal">nprobes</th>
              <th className="text-right pr-5 font-normal">recall</th>
              <th className="text-right pr-5 font-normal">per search</th>
              <th className="text-right font-normal">ms</th>
            </tr>
          </thead>
          <tbody className="text-[var(--bright)]">
            {curve.map((p) => (
              <tr key={String(p.nprobes)}>
                <td className="pr-5">{p.nprobes ?? "default"}</td>
                <td className="text-right pr-5">{pct(p.recall)}</td>
                <td className="text-right pr-5">{bytes(p.read_bytes)}</td>
                <td className="text-right">{p.ms}</td>
              </tr>
            ))}
            <tr className="text-[var(--haze)]">
              <td className="pr-5">exact</td>
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
