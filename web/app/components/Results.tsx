"use client";

import { fmtClock, type Hit } from "@/app/lib/api";

export default function Results({
  hits, onPick,
}: { hits: Hit[]; onPick: (h: Hit) => void }) {
  if (!hits.length) return null;

  return (
    <div className="grid gap-4 grid-cols-[repeat(auto-fill,minmax(330px,1fr))]">
      {hits.map((h, i) => (
        <button
          key={h.moment_id}
          onClick={() => onPick(h)}
          className="panel overflow-hidden text-left group transition-colors
                     hover:border-[var(--video)] focus:border-[var(--video)]
                     focus:outline-none"
        >
          <div className="relative aspect-video bg-black">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            {h.thumb && (
              <img src={h.thumb} alt="" className="w-full h-full object-cover" />
            )}
            <div className="absolute bottom-2 right-2 mono text-[11px] px-2 py-1 rounded-sm
                            bg-[rgba(23,21,19,0.9)] text-[var(--video)]">
              {fmtClock(h.ts_s)}
            </div>
            {/* First result is what the presenter opens with Enter. */}
            {i < 9 && (
              <div className="absolute top-2 left-2 mono text-[10px] w-5 h-5 grid place-items-center
                              rounded-sm bg-[rgba(23,21,19,0.9)] text-[var(--haze)]">
                {i + 1}
              </div>
            )}
          </div>
          <div className="p-3.5">
            <div className="text-[15px] leading-snug text-[var(--bright)] font-medium line-clamp-2">
              {h.title}
            </div>
            <div className="eyebrow mt-1.5 normal-case">
              {h.speaker || "unknown"} &middot; {h.year}
            </div>
            {h.transcript && (
              <p className="text-[13px] mt-2.5 line-clamp-2 leading-relaxed text-[var(--haze)]">
                {h.transcript}
              </p>
            )}
          </div>
        </button>
      ))}
    </div>
  );
}
