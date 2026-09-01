"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ByteScale from "@/app/components/ByteScale";
import Player from "@/app/components/Player";
import Results from "@/app/components/Results";
import SchemaView from "@/app/components/SchemaView";
import { search, resetMeter, type Hit, type MeterState, type SearchResponse } from "@/app/lib/api";

const MODES = [
  { id: "vector", label: "Semantic", hint: "text matched against the frame itself" },
  { id: "fts", label: "Full text", hint: "BM25 over the transcripts" },
  { id: "hybrid", label: "Hybrid", hint: "both, fused by rank" },
];

// The run of queries the talk walks through, in order. Keys 1-4 fire them.
const CUES = [
  "a diagram with boxes and arrows",
  "a terminal full of code",
  "a benchmark chart with bars",
  "a slide with a bulleted list",
];

export default function Page() {
  const [q, setQ] = useState("");
  const [mode, setMode] = useState("vector");
  const [hits, setHits] = useState<Hit[]>([]);
  const [meta, setMeta] = useState<SearchResponse | null>(null);
  const [meter, setMeter] = useState<MeterState | null>(null);
  const [busy, setBusy] = useState(false);
  const [picked, setPicked] = useState<Hit | null>(null);
  const [showSchema, setShowSchema] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [tracks, setTracks] = useState<string[]>([]);
  const [track, setTrack] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Open on a wall of real moments rather than an empty page.
  useEffect(() => {
    fetch("/api/sample?n=40")
      .then((r) => r.json())
      .then((d) => {
        if (d.hits?.length) {
          setHits(d.hits);
          setBrowsing(true);
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetch("/api/tracks")
      .then((r) => r.json())
      .then((d) => setTracks(d.tracks ?? []))
      .catch(() => setTracks([]));
  }, []);

  // Poll rather than stream: the counter has to keep moving while the browser
  // pulls video ranges, and a 300ms poll against localhost has far fewer failure
  // modes on a stage than an SSE stream through a dev proxy.
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const res = await fetch("/api/meter", { cache: "no-store" });
        if (!res.ok) throw new Error();
        if (alive) {
          setMeter(await res.json());
          setOffline(false);
        }
      } catch {
        if (alive) setOffline(true);
      }
    };
    tick();
    const id = setInterval(tick, 300);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const run = useCallback(async (query: string, m: string, t?: string | null) => {
    if (!query.trim()) return;
    setBusy(true);
    try {
      const res = await search({ q: query, mode: m, limit: 24, track: t ?? null });
      setHits(res.hits);
      setBrowsing(false);
      setMeta(res);
      setMeter(res.meter);
      setOffline(false);
    } catch {
      setOffline(true);
    } finally {
      setBusy(false);
    }
  }, []);

  // Presenter control. Everything reachable without looking at the keyboard.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = document.activeElement === inputRef.current;
      if (e.key === "/" && !typing) {
        e.preventDefault();
        inputRef.current?.focus();
        return;
      }
      if (typing && e.key !== "Escape") return;
      if (picked) return;
      if (e.key.toLowerCase() === "s") {
        setShowSchema((v) => !v);
        return;
      }
      if (showSchema) return;

      const n = parseInt(e.key, 10);
      if (n >= 1 && n <= CUES.length) {
        setQ(CUES[n - 1]);
        run(CUES[n - 1], mode, track);
      } else if (e.key.toLowerCase() === "r") {
        resetMeter().then(setMeter);
      } else if ((e.key === "Enter" || e.code === "NumpadEnter") && hits.length) {
        setPicked(hits[0]);
      } else if (e.key === "Escape") {
        inputRef.current?.blur();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [run, mode, track, hits, picked, showSchema]);

  return (
    <main className="relative z-10 min-h-screen px-[var(--stage-pad)] pt-7 pb-[210px]">
      <header className="flex items-baseline justify-between mb-8">
        <div className="flex items-baseline gap-4">
          <h1 className="text-[19px] font-bold tracking-tight text-[var(--bright)]">
            Ctrl&#8209;F for Video
          </h1>
          <span className="eyebrow normal-case">
            the video and its index are the same table
          </span>
        </div>
        <div className="flex items-center gap-4">
          {offline && (
            <span className="mono text-[10px] px-2.5 py-1 rounded-sm"
                  style={{ background: "rgba(255,115,74,0.14)", color: "var(--video)" }}>
              API NOT RESPONDING
            </span>
          )}
          <span className="eyebrow">
            1&ndash;4 cues &middot; / search &middot; &crarr; open &middot; S schema &middot; R reset
          </span>
        </div>
      </header>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          run(q, mode, track);
        }}
      >
        <input
          ref={inputRef}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Describe what you want to see — or press / to type"
          className="w-full bg-[var(--ink-3)] border border-[var(--rule)] rounded-sm
                     px-6 py-5 text-[26px] text-[var(--bright)] outline-none
                     focus:border-[var(--video)] transition-colors
                     placeholder:text-[#5c524c]"
        />
      </form>

      <div className="flex flex-wrap items-center gap-2 mt-4 mb-7">
        {MODES.map((m) => (
          <button
            key={m.id}
            title={m.hint}
            onClick={() => {
              setMode(m.id);
              if (q.trim()) run(q, m.id, track);
            }}
            className="mono text-[10px] tracking-[0.14em] uppercase px-3.5 py-2
                       rounded-sm border transition-colors"
            style={
              mode === m.id
                ? { borderColor: "var(--video)", color: "var(--video)",
                    background: "rgba(255,115,74,0.09)" }
                : { borderColor: "var(--rule)", color: "var(--haze)" }
            }
          >
            {m.label}
          </button>
        ))}

        <div className="w-px h-6 bg-[var(--rule)] mx-2" />

        {CUES.map((c, i) => (
          <button
            key={c}
            onClick={() => {
              setQ(c);
              run(c, mode, track);
            }}
            className="group text-[13px] px-3 py-2 rounded-sm border border-[var(--rule)]
                       text-[var(--haze)] hover:text-[var(--bright)]
                       hover:border-[var(--haze)] transition-colors"
          >
            <span className="mono text-[10px] mr-2 text-[#5c524c]">{i + 1}</span>
            {c}
          </button>
        ))}
      </div>

      {tracks.length > 1 && (
        <div className="flex flex-wrap items-center gap-2 mb-6">
          <span className="eyebrow mr-1">Devroom</span>
          <button
            onClick={() => {
              setTrack(null);
              if (q.trim()) run(q, mode, null);
            }}
            className="text-[12px] px-2.5 py-1.5 rounded-sm border transition-colors"
            style={
              track === null
                ? { borderColor: "var(--haze)", color: "var(--bright)" }
                : { borderColor: "var(--rule)", color: "var(--haze)" }
            }
          >
            All
          </button>
          {tracks.map((t) => (
            <button
              key={t}
              onClick={() => {
                const next = track === t ? null : t;
                setTrack(next);
                if (q.trim()) run(q, mode, next);
              }}
              className="text-[12px] px-2.5 py-1.5 rounded-sm border transition-colors"
              style={
                track === t
                  ? { borderColor: "var(--video)", color: "var(--video)",
                      background: "rgba(255,115,74,0.09)" }
                  : { borderColor: "var(--rule)", color: "var(--haze)" }
              }
            >
              {t}
            </button>
          ))}
        </div>
      )}

      {browsing && !busy && (
        <div className="mono text-[11px] mb-5 text-[var(--haze)]">
          {meter
            ? `${meter.corpus_moments.toLocaleString()} moments across ${meter.corpus_talks} talks — a sample`
            : "loading the corpus"}
          <span className="mx-3 text-[var(--rule)]">&middot;</span>
          press 1, or describe something you want to see
        </div>
      )}

      {meta && !busy && !browsing && (
        <div className="mono text-[11px] mb-5 text-[var(--haze)]">
          {hits.length} moments in {meta.ms}ms
          {track && (
            <>
              <span className="mx-3 text-[var(--rule)]">&middot;</span>
              <span style={{ color: "var(--video)" }}>
                filtered to {track} inside the search, not after it
              </span>
            </>
          )}
          <span className="mx-3 text-[var(--rule)]">&middot;</span>
          this query read{" "}
          <span style={{ color: "var(--index)" }}>
            {(meta.query_index_bytes / 1e6).toFixed(2)} MB
          </span>{" "}
          finding it and{" "}
          <span style={{ color: "var(--video)" }}>
            {meta.query_video_bytes === 0 ? "nothing" : `${meta.query_video_bytes} bytes`}
          </span>{" "}
          of video
        </div>
      )}

      {busy && (
        <div className="mono text-[11px] text-[var(--haze)] mb-5">searching&hellip;</div>
      )}

      {!hits.length && !busy && (
        <div className="mt-24 text-center">
          <p className="text-[15px] text-[var(--haze)] max-w-lg mx-auto leading-relaxed">
            No moments loaded. Build the corpus with{" "}
            <span className="mono text-[var(--bright)]">make ingest</span>, then reload.
          </p>
        </div>
      )}

      <Results hits={hits} onPick={setPicked} />
      {picked && <Player hit={picked} onClose={() => setPicked(null)} />}
      {showSchema && <SchemaView onClose={() => setShowSchema(false)} />}
      <ByteScale meter={meter} />
    </main>
  );
}
