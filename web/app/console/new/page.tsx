"use client";

/** Building a database from your own files.
 *
 *  A route rather than a tab in the console. The console's tabs are per selected
 *  table — schema, versions, indices *of this one* — and creating a database is not
 *  a thing you do to a table.
 *
 *  A job outlives the tab that started it, so `?job=<id>` rejoins one. Progress is
 *  polled rather than streamed: reading a job reads an in-memory dataclass, and an
 *  hour-long run is exactly where a dropped stream through a dev proxy costs most
 *  and buys least — the same call `web/app/demo/page.tsx` made about the meter.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { FoundSummary } from "@/app/components/ingest/FoundSummary";
import { DestinationStep } from "@/app/components/ingest/DestinationStep";
import { Outcome } from "@/app/components/ingest/Outcome";
import { RunPanel } from "@/app/components/ingest/RunPanel";
import { SourceStep } from "@/app/components/ingest/SourceStep";
import Icon from "@/app/components/Icon";
import AppBar from "@/app/components/nav/AppBar";
import { ApiError } from "@/app/lib/catalog";
import {
  adoptJob, cancelJob, discardJob, getIngestCapabilities, getJob, scanSource,
  startJob, LIVE_STATES,
  type IngestCapabilities, type Job, type MediaKind, type ScanResult,
} from "@/app/lib/ingest";
import { useSources } from "@/app/lib/sources";

const ALL_KINDS: MediaKind[] = ["image", "video", "audio", "pdf"];
const POLL_MS = 1000;
const SLOW_POLL_MS = 5000;

export default function NewDatabase() {
  const [caps, setCaps] = useState<IngestCapabilities | null>(null);
  const [source, setSource] = useState("");
  const [scan, setScan] = useState<ScanResult | null>(null);
  const [picked, setPicked] = useState<Set<MediaKind>>(new Set(ALL_KINDS));
  const [sampleOnly, setSampleOnly] = useState(false);
  const [name, setName] = useState("");
  const [parent, setParent] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { sources, remember } = useSources();
  const router = useRouter();
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    getIngestCapabilities()
      .then(setCaps)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, []);

  // Rejoin a job the URL names, so a reload or a second tab does not lose it.
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("job");
    if (id) getJob(id).then(setJob).catch(() => undefined);
  }, []);

  // Poll while live. Slower once a single file has been in flight for a while:
  // there is nothing new to learn every second about a file that takes minutes.
  useEffect(() => {
    if (!job || !LIVE_STATES.includes(job.state)) return;
    const slow = (job.progress.current_file_elapsed_s ?? 0) > 60;
    pollTimer.current = setTimeout(() => {
      getJob(job.id).then(setJob).catch(() => undefined);
    }, slow ? SLOW_POLL_MS : POLL_MS);
    return () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, [job]);

  const check = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await scanSource(source.trim());
      setScan(result);
      if (result.readable) {
        remember(result.source);
        if (!name) setName(result.source.split("/").filter(Boolean).pop() ?? "");
      }
      setPicked(new Set(
        result.found
          .filter((f) => result.readiness[f.kind]?.available !== false)
          .filter((f) => caps?.implemented.includes(f.kind) !== false)
          .map((f) => f.kind),
      ));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setScan(null);
    } finally {
      setBusy(false);
    }
  }, [source, remember, name, caps]);

  const begin = useCallback(async () => {
    if (!scan) return;
    setBusy(true);
    setError(null);
    try {
      const started = await startJob({
        source: scan.source,
        destination: (parent || caps?.destination_default || "").trim(),
        name: name.trim(),
        kinds: [...picked],
        limit: sampleOnly ? 20 : null,
      });
      setJob(started);
      const url = new URL(window.location.href);
      url.searchParams.set("job", started.id);
      window.history.replaceState({}, "", url);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [scan, parent, caps, name, picked, sampleOnly]);

  const toggle = useCallback((k: MediaKind) => {
    setPicked((now) => {
      const next = new Set(now);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
  }, []);

  const restart = useCallback(() => {
    setJob(null);
    setScan(null);
    setName("");
    const url = new URL(window.location.href);
    url.searchParams.delete("job");
    window.history.replaceState({}, "", url);
  }, []);

  const selectedFiles = (scan?.found ?? [])
    .filter((f) => picked.has(f.kind))
    .reduce((n, f) => n + f.files, 0);
  const willIngest = sampleOnly ? Math.min(20, selectedFiles) : selectedFiles;
  const live = job !== null && LIVE_STATES.includes(job.state);
  const finished = job !== null && !live;
  const cannotWrite = caps !== null && !caps.writes.available;
  const unimplemented = [...picked].filter((k) => !caps?.implemented.includes(k));

  return (
    <main className="mx-auto max-w-4xl px-6 py-10">
      <AppBar crumbs={[{ label: "Console", href: "/console" }, { label: "New database" }]} />

      {!job && (
        <p className="text-[14px] text-[var(--haze)] leading-relaxed max-w-2xl mb-8">
          Point this at a folder of your own media and it will build a Lance table you
          can search — by words, and by what the pictures look like.
        </p>
      )}

      {error && (
        <div className="mono flex items-start gap-2.5 text-[12px] px-3.5 py-3 rounded-sm mb-6"
             style={{ background: "rgb(var(--video-rgb) / 0.12)",
                      border: "1px solid rgb(var(--video-rgb) / 0.4)", color: "var(--video)" }}>
          <Icon name="warning" size={15} className="mt-0.5 shrink-0" />
          {error}
        </div>
      )}

      {live && <RunPanel job={job} onCancel={() => cancelJob(job.id).then(setJob)} />}

      {finished && (
        <Outcome
          job={job}
          onRestart={restart}
          onOpen={async () => {
            try {
              await adoptJob(job.id);
            } catch {
              // Adoption can decline (an env-locked root, say). The table is written
              // either way, and the console is still worth opening.
            }
            router.push(`/console?table=${encodeURIComponent(job.request.name)}`);
          }}
          onDiscard={async () => {
            try {
              await discardJob(job.id);
              restart();
            } catch (e) {
              setError(e instanceof ApiError ? e.message : String(e));
            }
          }}
        />
      )}

      {!job && (
        <>
          <SourceStep value={source} onChange={setSource} onCheck={check}
                      busy={busy} sources={sources} />

          {scan && (
            <div className="mt-10 pt-8" style={{ borderTop: "1px solid var(--rule)" }}>
              <FoundSummary scan={scan} picked={picked} onToggle={toggle} />
            </div>
          )}

          {scan && scan.found.length > 0 && (
            <div className="mt-10 pt-8" style={{ borderTop: "1px solid var(--rule)" }}>
              <DestinationStep
                name={name} onName={setName}
                parent={parent} onParent={setParent}
                defaultParent={caps?.destination_default ?? "~/LanceScope"}
                embedder={caps?.embedder ?? null}
                advanced={advanced} onAdvanced={setAdvanced}
              />

              <label className="flex items-center gap-2 mt-6 text-[13px] cursor-pointer">
                <input type="checkbox" checked={sampleOnly}
                       onChange={(e) => setSampleOnly(e.target.checked)} />
                <span>First 20 files only</span>
                <span className="text-[var(--haze)] text-[12px]">
                  — try it before committing to {selectedFiles.toLocaleString()}
                </span>
              </label>

              {unimplemented.length > 0 && (
                <p className="text-[12px] leading-relaxed mt-4" style={{ color: "var(--video)" }}>
                  {unimplemented.join(", ")} cannot be turned into rows yet and will be
                  left out of this run.
                </p>
              )}

              <div className="mt-7 flex items-center gap-4 flex-wrap">
                <button
                  className="btn btn-accent"
                  disabled={busy || cannotWrite || !name.trim() || willIngest === 0}
                  title={cannotWrite ? caps?.writes.reason : ""}
                  onClick={begin}
                >
                  <Icon name="plus" size={14} />
                  Build from {willIngest.toLocaleString()} file{willIngest === 1 ? "" : "s"}
                </button>
                {cannotWrite && (
                  <span className="text-[12px] text-[var(--haze)] leading-relaxed max-w-md">
                    {caps?.writes.reason}
                  </span>
                )}
              </div>
            </div>
          )}
        </>
      )}

      {caps && !job && (
        <div className="mt-12 pt-8" style={{ borderTop: "1px solid var(--rule)" }}>
          <div className="eyebrow mb-3">what this build can decode</div>
          <div className="flex flex-wrap gap-x-8 gap-y-2">
            {ALL_KINDS.map((k) => {
              const r = caps.media[k];
              const implemented = caps.implemented.includes(k);
              const ok = r?.available && implemented;
              return (
                <div key={k} className="mono text-[12px]"
                     title={implemented ? (r?.reason ?? "") : "no handler yet"}>
                  <span style={{ color: ok ? "var(--index)" : "var(--haze)" }}>
                    {ok ? "yes" : "no"}
                  </span>{" "}
                  <span className="text-[var(--body)]">{k}</span>
                </div>
              );
            })}
          </div>
          <p className="text-[12px] text-[var(--haze)] leading-relaxed mt-4 max-w-2xl">
            {caps.note} Files this build cannot read are reported at plan time rather
            than failing partway through a run.
          </p>
          <p className="text-[12px] text-[var(--haze)] mt-6">
            <Link href="/console" className="underline" style={{ color: "var(--video)" }}>
              Back to the console
            </Link>
          </p>
        </div>
      )}
    </main>
  );
}
