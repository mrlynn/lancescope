"use client";

import { fmtClock, type Hit } from "@/app/lib/api";

export default function Results({
  hits, onPick,
}: { hits: Hit[]; onPick: (h: Hit) => void }) {
  if (!hits.length) return null;

  return (
    <div className="grid gap-5 grid-cols-[repeat(auto-fill,minmax(300px,1fr))] pb-56">
      {hits.map((h) => (
        <button
          key={h.moment_id}
          onClick={() => onPick(h)}
          className="card overflow-hidden text-left group hover:border-[var(--index)]
                     transition-colors focus:outline-none focus:border-[var(--index)]"
        >
          <div className="relative aspect-video bg-black">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={`/api${h.thumb_url}`}
              alt=""
              className="w-full h-full object-cover"
              loading="lazy"
            />
            <div className="absolute bottom-2 right-2 mono text-xs px-2 py-1 rounded
                            bg-black/80 text-[var(--video)] tabular">
              {fmtClock(h.ts_s)}
            </div>
          </div>
          <div className="p-3.5">
            <div className="font-medium text-[15px] leading-snug line-clamp-2">
              {h.title}
            </div>
            <div className="mono text-[11px] text-[var(--muted)] mt-1.5">
              {h.speaker || "unknown"} · {h.year}
            </div>
            {h.transcript && (
              <p className="text-[13px] text-[var(--muted)] mt-2.5 line-clamp-2 leading-relaxed">
                {h.transcript}
              </p>
            )}
          </div>
        </button>
      ))}
    </div>
  );
}
