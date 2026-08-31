"use client";

import { useCallback, useEffect, useState } from "react";
import Hud from "@/app/components/Hud";
import Player from "@/app/components/Player";
import Results from "@/app/components/Results";
import { search, type Hit, type MeterState, type SearchResponse } from "@/app/lib/api";

const MODES = [
  { id: "vector", label: "SEMANTIC", hint: "text → frame, via SigLIP" },
  { id: "fts", label: "FULL TEXT", hint: "BM25 over transcripts" },
  { id: "hybrid", label: "HYBRID", hint: "both, fused by rank" },
];

// The three queries the talk walks through, in order.
const SUGGESTIONS = [
  "a diagram with boxes and arrows",
  "source code on screen",
  "a slide with a bulleted list",
  "kubernetes containers",
];

export default function Page() {
  const [q, setQ] = useState("");
  const [mode, setMode] = useState("vector");
  const [hits, setHits] = useState<Hit[]>([]);
  const [meta, setMeta] = useState<SearchResponse | null>(null);
  const [meter, setMeter] = useState<MeterState | null>(null);
  const [busy, setBusy] = useState(false);
  const [picked, setPicked] = useState<Hit | null>(null);

  // Live meter. Polled rather than streamed: the counter has to keep moving while
  // the browser pulls video ranges, and a 300ms poll against localhost is free and
  // has far fewer failure modes on a stage than an SSE stream through a dev proxy.
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const res = await fetch("/api/meter", { cache: "no-store" });
        if (alive) setMeter(await res.json());
      } catch {
        /* server restarting; try again on the next tick */
      }
    };
    tick();
    const id = setInterval(tick, 300);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const run = useCallback(
    async (query: string, m: string) => {
      if (!query.trim()) return;
      setBusy(true);
      try {
        const res = await search({ q: query, mode: m, limit: 24 });
        setHits(res.hits);
        setMeta(res);
        setMeter(res.meter);
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  return (
    <main className="min-h-screen px-8 py-7">
      <header className="mb-7">
        <h1 className="text-[26px] font-semibold tracking-tight">
          Ctrl-F for Video
        </h1>
        <p className="mono text-[13px] text-[var(--muted)] mt-1.5">
          the video and its index are the same table
        </p>
      </header>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          run(q, mode);
        }}
        className="mb-4"
      >
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="describe what you want to see&hellip;"
          autoFocus
          className="w-full card px-5 py-4 text-xl outline-none
                     focus:border-[var(--index)] transition-colors
                     placeholder:text-[#4a5162]"
        />
      </form>

      <div className="flex flex-wrap items-center gap-2.5 mb-5">
        {MODES.map((m) => (
          <button
            key={m.id}
            onClick={() => {
              setMode(m.id);
              if (q.trim()) run(q, m.id);
            }}
            title={m.hint}
            className="mono text-[11px] tracking-[0.14em] px-3.5 py-2 rounded-md border transition-colors"
            style={
              mode === m.id
                ? { borderColor: "var(--index)", color: "var(--index)",
                    background: "rgba(76,201,240,0.08)" }
                : { borderColor: "var(--line)", color: "var(--muted)" }
            }
          >
            {m.label}
          </button>
        ))}

        <div className="w-px h-6 bg-[var(--line)] mx-1.5" />

        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            onClick={() => {
              setQ(s);
              run(s, mode);
            }}
            className="text-[12px] px-3 py-2 rounded-md border border-[var(--line)]
                       text-[var(--muted)] hover:text-[var(--text)]
                       hover:border-[var(--muted)] transition-colors"
          >
            {s}
          </button>
        ))}
      </div>

      {meta && (
        <div className="mono text-[12px] text-[var(--muted)] mb-5 tabular">
          {hits.length} moments in {meta.ms}ms
          <span className="mx-2.5 text-[var(--line)]">|</span>
          this query read{" "}
          <span style={{ color: "var(--index)" }}>
            {(meta.query_index_bytes / 1e6).toFixed(2)} MB
          </span>{" "}
          of index and{" "}
          <span style={{ color: meta.query_video_bytes ? "var(--hot)" : "var(--video)" }}>
            {meta.query_video_bytes} bytes
          </span>{" "}
          of video
        </div>
      )}

      {busy && !hits.length && (
        <div className="mono text-sm text-[var(--muted)]">searching&hellip;</div>
      )}

      <Results hits={hits} onPick={setPicked} />
      {picked && <Player hit={picked} onClose={() => setPicked(null)} />}
      <Hud meter={meter} />
    </main>
  );
}
