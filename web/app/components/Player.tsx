"use client";

import { useEffect, useRef } from "react";
import { fmtClock, type Hit } from "@/app/lib/api";

export default function Player({ hit, onClose }: { hit: Hit; onClose: () => void }) {
  const ref = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    // Seek inside the segment. The browser issues its own ranged requests to get
    // here, and each one becomes a narrow read out of the Lance blob column.
    const seek = () => {
      // Start a few seconds early so the moment plays with a run-up instead of
      // landing on the last frame of the segment.
      const LEAD_IN = 5;
      v.currentTime = Math.max(0, hit.segment_offset_s - LEAD_IN);
      v.play().catch(() => {});
    };
    v.addEventListener("loadedmetadata", seek, { once: true });
    return () => v.removeEventListener("loadedmetadata", seek);
  }, [hit]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center p-8 pt-10"
      style={{ background: "rgba(4,5,8,0.9)" }}
      onClick={onClose}
    >
      <div className="w-full max-w-4xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-end justify-between mb-3 gap-6">
          <div className="min-w-0">
            <div className="text-2xl font-semibold truncate">{hit.title}</div>
            <div className="mono text-sm text-[var(--muted)] mt-1">
              {hit.speaker} · {hit.year} · moment at {fmtClock(hit.ts_s)}
            </div>
          </div>
          <button
            onClick={onClose}
            className="mono text-xs tracking-widest px-3 py-2 rounded border
                       border-[var(--line)] text-[var(--muted)] shrink-0"
          >
            ESC
          </button>
        </div>

        <video
          ref={ref}
          src={`/api${hit.video_url}`}
          controls
          autoPlay
          className="w-full rounded-lg border border-[var(--line)] bg-black"
        />

        <div className="mono text-xs text-[var(--muted)] mt-3">
          segment {hit.segment_idx} of {hit.talk_id} · seeking to +
          {hit.segment_offset_s.toFixed(1)}s · streamed by HTTP Range out of a Blob V2
          column — watch the VIDEO counter
        </div>
      </div>
    </div>
  );
}
