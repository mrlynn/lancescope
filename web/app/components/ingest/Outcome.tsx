"use client";

/** What happened, in four flavours — done, done-with-failures, cancelled, failed.
 *
 *  Each renders the server's `detail` verbatim rather than composing its own
 *  sentence. That field is written where the facts are, and a UI that paraphrases
 *  it is a UI that will eventually paraphrase it wrong — telling someone their rows
 *  were rolled back when a committed Lance append cannot be taken back.
 *
 *  Keep and Discard are two different actions with two different consequences, and
 *  Discard only renders for a table this job actually created.
 */

import Link from "next/link";
import { useState } from "react";

import Icon from "@/app/components/Icon";
import type { Job } from "@/app/lib/ingest";

const TONE: Record<string, { color: string; icon: "check" | "warning" | "info" }> = {
  done: { color: "var(--index)", icon: "check" },
  cancelled: { color: "var(--video)", icon: "info" },
  failed: { color: "var(--video)", icon: "warning" },
  interrupted: { color: "var(--video)", icon: "info" },
};

export function Outcome({
  job, onOpen, onDiscard, onRestart,
}: {
  job: Job;
  onOpen: () => void;
  onDiscard: () => void;
  onRestart: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const r = job.result;
  const partial = Boolean(r?.partial);
  const tone = TONE[job.state] ?? TONE.done;
  const wrote = Boolean(r && r.rows > 0);
  const canDiscard = Boolean(r?.created && wrote);

  return (
    <section>
      <div className="flex items-start gap-2.5 mb-4" style={{ color: tone.color }}>
        <Icon name={tone.icon} size={16} className="mt-0.5 shrink-0" />
        <div className="eyebrow" style={{ color: tone.color }}>
          {job.state === "done" && !partial ? "done"
            : job.state === "done" ? "done, with failures"
            : job.state}
        </div>
      </div>

      {/* Verbatim. The facts live where this sentence was written. */}
      <p className="text-[14px] leading-relaxed max-w-2xl">{job.detail || job.error}</p>

      {r && wrote && (
        <div className="flex flex-wrap gap-x-7 gap-y-2 mt-5 mono text-[11px] text-[var(--haze)]">
          <span><span className="text-[var(--bright)]">{r.rows.toLocaleString()}</span> rows</span>
          <span>version {r.version}</span>
          {r.vector_dim && <span>{r.vector_dim}-dim vectors</span>}
          {r.embedder && <span>{r.embedder.model}</span>}
          <span>{(r.ms / 1000).toFixed(1)}s</span>
        </div>
      )}

      {r && r.indices.length > 0 && (
        <div className="mt-5">
          <div className="eyebrow mb-2">indices</div>
          {r.indices.map((i) => (
            <p key={i.column} className="mono text-[11px] leading-relaxed">
              <span style={{ color: i.built ? "var(--index)" : "var(--haze)" }}>
                {i.built ? "built" : "skipped"}
              </span>{" "}
              <span className="text-[var(--body)]">{i.column}</span>
              {i.reason && <span className="text-[var(--haze)]"> — {i.reason}</span>}
            </p>
          ))}
        </div>
      )}

      {r?.warnings.map((w) => (
        <p key={w} className="text-[12px] leading-relaxed mt-3 flex gap-2 items-start"
           style={{ color: "var(--video)" }}>
          <Icon name="warning" size={14} className="mt-0.5 shrink-0" />
          {w}
        </p>
      ))}

      {r && r.failures.length > 0 && (
        <details className="mt-5">
          <summary className="eyebrow cursor-pointer">
            {r.failures_total} file{r.failures_total === 1 ? "" : "s"} failed
          </summary>
          <div className="mt-2">
            {r.failures.map((f) => (
              <p key={f.path} className="mono text-[11px] text-[var(--haze)] truncate">
                {f.path.split("/").pop()} — {f.reason}
              </p>
            ))}
            {r.failures_total > r.failures.length && (
              <p className="mono text-[11px] text-[var(--dim)]">
                and {r.failures_total - r.failures.length} more
              </p>
            )}
          </div>
        </details>
      )}

      <div className="mt-8 flex items-center gap-3 flex-wrap">
        {wrote && (
          <button className="btn btn-accent" disabled={busy !== null}
                  onClick={async () => { setBusy("open"); await onOpen(); }}>
            <Icon name="database" size={14} />
            {busy === "open" ? "Opening…" : "Open in the console"}
          </button>
        )}
        {canDiscard && (
          <button className="btn" disabled={busy !== null}
                  onClick={async () => { setBusy("discard"); await onDiscard(); }}>
            <Icon name="trash" size={14} />
            Discard it
          </button>
        )}
        <button className="btn" onClick={onRestart}>Start another</button>
        {!wrote && (
          <Link href="/console" className="text-[12px] underline"
                style={{ color: "var(--video)" }}>Back to the console</Link>
        )}
      </div>

      {canDiscard && (
        <p className="text-[12px] text-[var(--haze)] leading-relaxed mt-4 max-w-xl">
          Discarding deletes <span className="mono">{r?.uri.split("/").pop()}</span>,
          which this run created. Nothing else is touched.
        </p>
      )}
    </section>
  );
}
