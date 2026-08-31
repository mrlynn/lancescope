"use client";

import { useEffect, useRef, useState } from "react";
import { fmtBytes, resetMeter, type MeterState } from "@/app/lib/api";

function Counter({
  label, bytes, iops, color, sub,
}: { label: string; bytes: number; iops: number; color: string; sub: string }) {
  const { value, unit } = fmtBytes(bytes);
  const [pulse, setPulse] = useState(0);
  const prev = useRef(bytes);
  useEffect(() => {
    if (bytes !== prev.current) {
      prev.current = bytes;
      setPulse((p) => p + 1);
    }
  }, [bytes]);

  return (
    <div className="flex-1">
      <div className="mono text-[11px] tracking-[0.18em] mb-1" style={{ color }}>
        {label}
      </div>
      <div key={pulse} className="ticking flex items-baseline gap-1.5">
        <span className="mono tabular text-5xl font-semibold leading-none" style={{ color }}>
          {value}
        </span>
        <span className="mono text-lg" style={{ color, opacity: 0.65 }}>{unit}</span>
      </div>
      <div className="mono text-[11px] text-[var(--muted)] mt-1.5">
        {iops} reads · {sub}
      </div>
    </div>
  );
}

export default function Hud({ meter }: { meter: MeterState | null }) {
  if (!meter) return null;

  const corpus = meter.corpus_video_bytes || 1;
  const moved = meter.index_bytes + meter.video_bytes;
  const pct = (moved / corpus) * 100;
  const corpusFmt = fmtBytes(corpus);

  return (
    <div
      // z-60 keeps the meter above the player: the audience has to watch the VIDEO
      // counter move while the clip is actually playing.
      className="fixed bottom-5 right-5 z-[60] card px-6 py-5 w-[430px] shadow-2xl"
      style={{ background: "rgba(16,18,24,0.97)", backdropFilter: "blur(8px)" }}
    >
      <div className="flex items-center justify-between mb-4">
        <div className="mono text-[11px] tracking-[0.22em] text-[var(--muted)]">
          BYTES READ FROM LANCE
        </div>
        <button
          onClick={() => resetMeter()}
          className="mono text-[10px] tracking-widest px-2.5 py-1 rounded border
                     border-[var(--line)] text-[var(--muted)] hover:text-[var(--text)]
                     hover:border-[var(--muted)] transition-colors"
        >
          RESET
        </button>
      </div>

      <div className="flex gap-7">
        <Counter
          label="INDEX" color="var(--index)"
          bytes={meter.index_bytes} iops={meter.index_iops} sub="finding it"
        />
        <div className="w-px bg-[var(--line)]" />
        <Counter
          label="VIDEO" color="var(--video)"
          bytes={meter.video_bytes} iops={meter.video_iops} sub="playing it"
        />
      </div>

      {/* Proportion of the whole corpus that has actually moved. At demo scale this
          bar is essentially invisible, which is the point. */}
      <div className="mt-5">
        <div className="h-2 rounded-full bg-[var(--panel-2)] overflow-hidden flex">
          <div
            style={{ width: `${Math.min(100, (meter.index_bytes / corpus) * 100)}%`,
                     background: "var(--index)" }}
          />
          <div
            style={{ width: `${Math.min(100, (meter.video_bytes / corpus) * 100)}%`,
                     background: "var(--video)" }}
          />
        </div>
        <div className="mono text-[11px] text-[var(--muted)] mt-2 flex justify-between">
          <span>
            {pct < 0.01 ? "<0.01" : pct.toFixed(2)}% of corpus
          </span>
          <span>
            {meter.corpus_talks} talks · {corpusFmt.value} {corpusFmt.unit} of video
          </span>
        </div>
      </div>
    </div>
  );
}
