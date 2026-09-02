"use client";

/** A running ingest.
 *
 *  Two clocks, not one. The bar counts files, and `current_file` carries its own
 *  elapsed time — without that, a single ninety-minute video is indistinguishable
 *  from a hang, and the person watching reaches for the wrong remedy.
 *
 *  The ETA appears only once ten files are done and says it is an estimate. Cancel
 *  is labelled with what it actually does, because the honest answer is "after the
 *  current file", not "now".
 */

import { Bytes } from "@/app/components/console/atoms";
import Icon from "@/app/components/Icon";
import { fmtBytes } from "@/app/lib/api";
import type { Job } from "@/app/lib/ingest";

function fmtEta(s: number): string {
  if (s < 90) return `${Math.round(s)}s`;
  if (s < 5400) return `${Math.round(s / 60)}m`;
  return `${(s / 3600).toFixed(1)}h`;
}

export function RunPanel({ job, onCancel }: { job: Job; onCancel: () => void }) {
  const p = job.progress;
  const pct = p.files_total ? Math.round((p.files_done / p.files_total) * 100) : 0;
  const stopping = job.state === "cancelling";

  return (
    <section>
      <div className="eyebrow mb-3">{stopping ? "stopping" : p.stage}</div>

      <div className="h-1.5 rounded-sm overflow-hidden" style={{ background: "var(--rule)" }}>
        <div className="h-full transition-[width] duration-300"
             style={{ width: `${pct}%`, background: "var(--index)" }} />
      </div>

      <div className="flex flex-wrap gap-x-7 gap-y-2 mt-4 mono text-[11px] text-[var(--haze)]">
        <span>
          <span className="text-[var(--bright)]">{p.files_done.toLocaleString()}</span>
          {" / "}{p.files_total.toLocaleString()} files
        </span>
        <span><span className="text-[var(--bright)]">{p.rows_written.toLocaleString()}</span> rows</span>
        <span>read <Bytes n={p.source_bytes_read} /></span>
        {p.files_failed > 0 && (
          <span style={{ color: "var(--video)" }}>{p.files_failed} failed</span>
        )}
        {p.eta_s !== null && (
          <span title="estimated from the last ten files">~{fmtEta(p.eta_s)} left</span>
        )}
      </div>

      {p.current_file && (
        <p className="mono text-[11px] text-[var(--haze)] mt-3 truncate">
          {p.current_file.split("/").pop()}
          {p.current_file_elapsed_s !== null && p.current_file_elapsed_s > 3 && (
            <span className="text-[var(--dim)]"> · {Math.round(p.current_file_elapsed_s)}s</span>
          )}
        </p>
      )}

      <div className="mt-6 flex items-center gap-4 flex-wrap">
        <button className="btn" onClick={onCancel} disabled={stopping}>
          <Icon name="close" size={14} />
          {stopping ? "Stopping…" : "Stop after the current file"}
        </button>
        <span className="text-[12px] text-[var(--haze)] leading-relaxed max-w-md">
          {stopping
            ? job.detail
            : `Rows are committed in batches, so stopping keeps everything written so far — ${fmtBytes(p.source_bytes_read).value} ${fmtBytes(p.source_bytes_read).unit} read so far.`}
        </span>
      </div>
    </section>
  );
}
