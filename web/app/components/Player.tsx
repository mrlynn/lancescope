"use client";

import { useEffect, useRef, useState } from "react";
import Icon from "@/app/components/Icon";
import { fmtClock, type Hit } from "@/app/lib/api";

const LEAD_IN = 5;

export default function Player({ hit, onClose }: { hit: Hit; onClose: () => void }) {
  const ref = useRef<HTMLVideoElement>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === " ") {
        e.preventDefault();
        const v = ref.current;
        if (v) v.paused ? v.play() : v.pause();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    setFailed(false);
    // Start a few seconds early so the moment plays with a run-up rather than
    // landing on the last frame of its segment.
    const seek = () => {
      v.currentTime = Math.max(0, hit.segment_offset_s - LEAD_IN);
      v.play().catch(() => {});
    };
    v.addEventListener("loadedmetadata", seek, { once: true });
    return () => v.removeEventListener("loadedmetadata", seek);
  }, [hit]);

  return (
    <div
      className="fixed inset-0 z-[55] flex items-start justify-center p-8 pt-12"
      style={{ background: "var(--scrim-2)" }}
      onClick={onClose}
    >
      <div
        className="w-full"
        style={{ maxWidth: "min(1100px, 74vw)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-end justify-between mb-3 gap-8">
          <div className="min-w-0">
            <div className="text-[22px] font-medium text-[var(--bright)] truncate">
              {hit.title}
            </div>
            <div className="eyebrow mt-1.5 normal-case">
              {hit.speaker} &middot; {hit.year} &middot; moment at {fmtClock(hit.ts_s)}
            </div>
          </div>
          <button
            onClick={onClose}
            className="iconbtn shrink-0"
            data-tip="Close — ESC"
            data-tip-side="left"
            aria-label="Close"
          >
            <Icon name="close" size={16} />
          </button>
        </div>

        {failed ? (
          <div className="panel aspect-video grid place-items-center text-center px-10">
            <div>
              <div className="text-[var(--video)] mono text-sm mb-2">
                This segment did not load
              </div>
              <p className="text-sm text-[var(--haze)]">
                The API may have restarted. Press Escape and run the search again.
              </p>
            </div>
          </div>
        ) : (
          <video
            ref={ref}
            src={`/api${hit.video_url}`}
            controls
            autoPlay
            onError={() => setFailed(true)}
            className="w-full rounded-sm border border-[var(--rule)] bg-black"
          />
        )}

        <div className="eyebrow mt-3 normal-case">
          segment {hit.segment_idx} &middot; seeking to +{hit.segment_offset_s.toFixed(1)}s
          &middot; streamed by HTTP Range straight out of a Blob V2 column
        </div>
      </div>
    </div>
  );
}
