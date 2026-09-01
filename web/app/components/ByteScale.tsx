"use client";

import { useEffect, useRef, useState } from "react";
import { fmtBytes, resetMeter, type MeterState } from "@/app/lib/api";

/* The instrument the whole talk rests on.

   A linear bar cannot show this: at demo scale the query is orders of magnitude
   smaller than the corpus, so a proportional bar renders as nothing at all. The
   rail is log-scaled from 1 KB to the size of the corpus, with the corpus pinned
   at the right end. The audience reads distance, not width — and the distance
   between "everything we have" and "what we touched" is the point.

   Zero is not plotted. Zero gets its own state, because "we read none of it" is a
   different claim from "we read very little of it", and the talk turns on it. */

const FLOOR = 1000; // 1 KB — the left end of the rail

function logPos(bytes: number, corpus: number): number {
  const lo = Math.log10(FLOOR);
  const hi = Math.log10(Math.max(corpus, FLOOR * 10));
  const v = Math.log10(Math.max(bytes, FLOOR));
  return Math.min(1, Math.max(0, (v - lo) / (hi - lo)));
}

/** Keep a label from hanging off either end of the rail. */
function clampShift(pct: number): string {
  if (pct < 8) return "0%";
  if (pct > 92) return "-100%";
  return "-50%";
}

function Readout({
  bytes, corpus, color, label, placement,
}: {
  bytes: number; corpus: number; color: string; label: string;
  placement: "above" | "below";
}) {
  const zero = bytes === 0;
  const pct = zero ? 0 : logPos(bytes, corpus) * 100;
  const { value, unit } = fmtBytes(bytes);

  return (
    <div
      className="absolute transition-all duration-700 ease-out"
      style={{
        left: `${pct}%`,
        transform: `translateX(${clampShift(pct)})`,
        ...(placement === "above" ? { bottom: "calc(50% + 26px)" } : { top: "calc(50% + 26px)" }),
      }}
    >
      <div className="flex flex-col whitespace-nowrap">
        <span className="eyebrow mb-1" style={{ color }}>{label}</span>
        {zero ? (
          <span className="mono font-bold leading-none" style={{ fontSize: 42, color }}>
            NONE
          </span>
        ) : (
          <span className="flex items-baseline gap-1.5">
            <span className="mono font-bold leading-none" style={{ fontSize: 42, color }}>
              {value}
            </span>
            <span className="mono text-lg" style={{ color, opacity: 0.7 }}>{unit}</span>
          </span>
        )}
      </div>
    </div>
  );
}

function Needle({ bytes, corpus, color }: { bytes: number; corpus: number; color: string }) {
  const zero = bytes === 0;
  const pct = zero ? 0 : logPos(bytes, corpus) * 100;
  return (
    <div
      className="absolute transition-all duration-700 ease-out"
      style={{ left: `${pct}%`, top: -13, height: 28, transform: "translateX(-50%)" }}
    >
      <div
        className={`w-[3px] h-full ${zero ? "breathe" : ""}`}
        style={{ background: color, boxShadow: `0 0 16px ${color}` }}
      />
    </div>
  );
}

export default function ByteScale({ meter }: { meter: MeterState | null }) {
  const [pulse, setPulse] = useState(0);
  const prev = useRef(0);

  useEffect(() => {
    if (!meter) return;
    const total = meter.index_bytes + meter.video_bytes;
    if (total !== prev.current) {
      prev.current = total;
      setPulse((p) => p + 1);
    }
  }, [meter]);

  if (!meter) return null;

  const corpus = Math.max(meter.corpus_video_bytes, 1);
  const corpusFmt = fmtBytes(corpus);

  const ticks: { pos: number; label: string }[] = [];
  for (let e = 3; e <= Math.floor(Math.log10(corpus)); e++) {
    const b = 10 ** e;
    const f = fmtBytes(b);
    ticks.push({ pos: logPos(b, corpus) * 100, label: `${f.value}${f.unit}` });
  }

  return (
    <div
      className="fixed bottom-0 left-0 right-0 z-[60] border-t border-[var(--rule)]"
      style={{ background: "var(--rail)", backdropFilter: "blur(10px)" }}
    >
      <div className="px-[var(--stage-pad)] pt-4 pb-6">
        <div className="flex items-center justify-between mb-1">
          <span className="eyebrow">Bytes read from Lance &middot; log scale</span>
          <div className="flex items-center gap-5">
            <span className="eyebrow">
              {meter.corpus_talks} talks &middot; {meter.corpus_moments.toLocaleString()} moments
              &middot; {corpusFmt.value} {corpusFmt.unit} of video
            </span>
            <button
              onClick={() => resetMeter()}
              className="mono text-[10px] tracking-[0.16em] px-3 py-1.5 rounded-sm border
                         border-[var(--rule)] text-[var(--haze)]
                         hover:text-[var(--bright)] hover:border-[var(--haze)]
                         transition-colors"
            >
              RESET &middot; R
            </button>
          </div>
        </div>

        {/* The rail. Fixed height so the two readouts have reserved room above and
            below and nothing reflows as the numbers change. */}
        <div key={pulse} className="relative h-[164px]">
          {/* right-hand gutter reserves space for the corpus anchor label */}
          <div className="absolute inset-y-0 left-0" style={{ right: 132 }}>
            <div className="relative w-full h-full">
              <div className="absolute left-0 right-0 top-1/2 h-[2px] bg-[var(--rule)]">
                {ticks.map((t) => (
                  <div key={t.label} className="absolute top-0" style={{ left: `${t.pos}%` }}>
                    <div className="w-px h-1.5 bg-[var(--rule)] -translate-x-1/2" />
                  </div>
                ))}

                {/* What we did NOT read, on the same scale. The honest
                    counterfactual: playing this moment could have meant moving a
                    whole talk. No strawman architecture required. */}
                {meter.median_talk_bytes > 0 && (
                  <div
                    className="absolute top-0"
                    style={{ left: `${logPos(meter.median_talk_bytes, corpus) * 100}%` }}
                  >
                    <div
                      className="w-px h-4 -translate-x-1/2 -translate-y-1"
                      style={{ background: "var(--rule)" }}
                    />
                    <span className="mono text-[9px] text-[var(--haze)] absolute top-4
                                     -translate-x-1/2 whitespace-nowrap">
                      one whole talk
                    </span>
                  </div>
                )}

                <div className="absolute left-full top-1/2 -translate-y-1/2 pl-4">
                  <div className="flex items-center gap-2.5">
                    <div className="w-2 h-2 rounded-full bg-[var(--bright)]" />
                    <div className="leading-tight">
                      <div className="mono text-[13px] text-[var(--bright)] whitespace-nowrap">
                        {corpusFmt.value} {corpusFmt.unit}
                      </div>
                      <div className="eyebrow">everything</div>
                    </div>
                  </div>
                </div>

                <Needle bytes={meter.index_bytes} corpus={corpus} color="var(--index)" />
                <Needle bytes={meter.video_bytes} corpus={corpus} color="var(--video)" />
              </div>

              <Readout
                bytes={meter.index_bytes} corpus={corpus}
                color="var(--index)" label="Finding it" placement="above"
              />
              <Readout
                bytes={meter.video_bytes} corpus={corpus}
                color="var(--video)" label="Playing it" placement="below"
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
