"use client";

/** Building a database from your own files.
 *
 *  A route rather than a tab in the console. The console's tabs are per selected
 *  table — schema, versions, indices *of this one* — and creating a database is not
 *  a thing you do to a table.
 *
 *  This build stops after the survey. `capabilities.writes` says why in a sentence,
 *  and the screen shows it rather than hiding: the useful half — what is in that
 *  folder and what this build could decode — answers fully, and a person who came
 *  here to find out whether it is worth installing ffmpeg gets their answer.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import AppBar from "@/app/components/nav/AppBar";
import { FoundSummary } from "@/app/components/ingest/FoundSummary";
import { SourceStep } from "@/app/components/ingest/SourceStep";
import Icon from "@/app/components/Icon";
import { ApiError } from "@/app/lib/catalog";
import {
  getIngestCapabilities,
  scanSource,
  type IngestCapabilities,
  type MediaKind,
  type ScanResult,
} from "@/app/lib/ingest";
import { useSources } from "@/app/lib/sources";

const ALL_KINDS: MediaKind[] = ["image", "video", "audio", "pdf"];

export default function NewDatabase() {
  const [caps, setCaps] = useState<IngestCapabilities | null>(null);
  const [source, setSource] = useState("");
  const [scan, setScan] = useState<ScanResult | null>(null);
  const [picked, setPicked] = useState<Set<MediaKind>>(new Set(ALL_KINDS));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { sources, remember } = useSources();

  useEffect(() => {
    getIngestCapabilities()
      .then(setCaps)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, []);

  const check = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await scanSource(source.trim());
      setScan(result);
      // Only a path that turned out to be real is worth offering back later.
      if (result.readable) remember(result.source);
      setPicked(new Set(
        result.found
          .filter((f) => result.readiness[f.kind]?.available !== false)
          .map((f) => f.kind),
      ));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setScan(null);
    } finally {
      setBusy(false);
    }
  }, [source, remember]);

  const toggle = useCallback((k: MediaKind) => {
    setPicked((now) => {
      const next = new Set(now);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
  }, []);

  const selected = scan?.found.filter((f) => picked.has(f.kind)) ?? [];
  const selectedFiles = selected.reduce((n, f) => n + f.files, 0);

  return (
    <main className="mx-auto max-w-4xl px-6 py-10">
      <AppBar crumbs={[{ label: "Console", href: "/console" }, { label: "New database" }]} />

      <p className="text-[14px] text-[var(--haze)] leading-relaxed max-w-2xl mb-8">
        Point this at a folder of your own media and it will tell you what is in
        there and what it could do with it.
      </p>

      {error && (
        <div className="mono flex items-center gap-2.5 text-[12px] px-3.5 py-3 rounded-sm mb-6"
             style={{ background: "rgb(var(--video-rgb) / 0.12)",
                      border: "1px solid rgb(var(--video-rgb) / 0.4)", color: "var(--video)" }}>
          <Icon name="warning" size={15} />
          {error}
        </div>
      )}

      <SourceStep
        value={source}
        onChange={setSource}
        onCheck={check}
        busy={busy}
        sources={sources}
      />

      {scan && (
        <div className="mt-10 pt-8" style={{ borderTop: "1px solid var(--rule)" }}>
          <FoundSummary scan={scan} picked={picked} onToggle={toggle} />
        </div>
      )}

      {scan && scan.found.length > 0 && (
        <div className="mt-10 pt-8" style={{ borderTop: "1px solid var(--rule)" }}>
          <div className="flex items-center gap-4 flex-wrap">
            <button className="btn btn-accent" disabled title={caps?.writes.reason ?? ""}>
              <Icon name="plus" size={14} />
              Build a database from {selectedFiles.toLocaleString()} file
              {selectedFiles === 1 ? "" : "s"}
            </button>
            {caps && !caps.writes.available && (
              <span className="text-[12px] text-[var(--haze)] leading-relaxed max-w-md">
                {caps.writes.reason}
              </span>
            )}
          </div>
        </div>
      )}

      {caps && (
        <div className="mt-12 pt-8" style={{ borderTop: "1px solid var(--rule)" }}>
          <div className="eyebrow mb-3">what this build can decode</div>
          <div className="flex flex-wrap gap-x-8 gap-y-2">
            {ALL_KINDS.map((k) => {
              const r = caps.media[k];
              return (
                <div key={k} className="mono text-[12px]" title={r?.reason ?? ""}>
                  <span style={{ color: r?.available ? "var(--index)" : "var(--haze)" }}>
                    {r?.available ? "yes" : "no"}
                  </span>{" "}
                  <span className="text-[var(--body)]">{k}</span>
                </div>
              );
            })}
          </div>
          {Object.values(caps.media).some((r) => !r.available) && (
            <p className="text-[12px] text-[var(--haze)] leading-relaxed mt-4 max-w-2xl">
              {caps.note} Missing decoders are reported here rather than partway
              through a run.
            </p>
          )}
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
