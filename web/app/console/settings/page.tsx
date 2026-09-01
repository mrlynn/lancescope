"use client";

/** Settings — the one page in the console that writes anything.
 *
 *  Two things are configured here and they are deliberately on one page: which
 *  database the console is reading, and how the intelligence layer is powered. Both
 *  used to be environment variables, which meant changing either one meant a
 *  restart, and neither could be discovered by looking at the app. */

import Image from "next/image";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Caveat, Empty, Eyebrow, fmtWhen } from "@/app/components/console/atoms";
import {
  type IntelProbe, type IntelligenceView, type Probe, type SettingsState,
  activateConnection, addConnection, getSettings, probeConnection, probeIntelligence,
  removeConnection, saveIntelligence,
} from "@/app/lib/settings";

export default function SettingsPage() {
  const [state, setState] = useState<SettingsState | null>(null);
  const [probe, setProbe] = useState<IntelProbe | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getSettings()
      .then(setState)
      .catch((e) => setError(e instanceof Error ? e.message : "settings unreachable"));
    probeIntelligence().then(setProbe).catch(() => setProbe(null));
  }, []);

  return (
    <main className="relative z-10 min-h-screen px-[var(--stage-pad)] pt-7 pb-16">
      <header className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-4">
          <a href="https://lancedb.com" target="_blank" rel="noreferrer" title="LanceDB"
             className="shrink-0 opacity-90 hover:opacity-100 transition-opacity">
            <Image src="/brand/lancedb-wordmark.png" alt="LanceDB" width={390} height={91}
                   priority className="h-[19px] w-auto" />
          </a>
          <div className="w-px h-5 bg-[var(--rule)]" />
          <h1 className="text-[19px] font-bold tracking-tight text-[var(--bright)]">Settings</h1>
        </div>
        <Link href="/console" className="pill">Console</Link>
      </header>

      {error && <Banner tone="video">{error}</Banner>}

      <div className="max-w-[860px] space-y-6">
        <Connections state={state} onChange={setState} onError={setError} />
        <Intelligence
          intel={state?.intelligence ?? null}
          probe={probe}
          onProbe={() => probeIntelligence().then(setProbe).catch(() => setProbe(null))}
          onSaved={(i) => setState((s) => (s ? { ...s, intelligence: i } : s))}
          onError={setError}
        />
        <Where state={state} />
      </div>
    </main>
  );
}

function Banner({ tone, children }: { tone: "video" | "index"; children: React.ReactNode }) {
  return (
    <div className="mono text-[12px] px-3.5 py-3 rounded-sm mb-6"
         style={{ background: `rgb(var(--${tone}-rgb) / 0.12)`,
                  border: `1px solid rgb(var(--${tone}-rgb) / 0.4)`,
                  color: `var(--${tone})` }}>
      {children}
    </div>
  );
}

/* ------------------------------------------------------------------- connections */

function Connections({ state, onChange, onError }: {
  state: SettingsState | null;
  onChange: (s: SettingsState) => void;
  onError: (e: string | null) => void;
}) {
  const [uri, setUri] = useState("");
  const [label, setLabel] = useState("");
  const [found, setFound] = useState<Probe | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async (fn: () => Promise<SettingsState>) => {
    setBusy(true);
    try {
      onChange(await fn());
      onError(null);
    } catch (e) {
      onError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  };

  const locked = state?.env_locked ?? false;

  return (
    <section className="panel p-6">
      <Eyebrow>Connections</Eyebrow>
      <p className="text-[13px] text-[var(--body)] leading-relaxed mb-5 max-w-[62ch]">
        Any directory holding <span className="mono text-[var(--bright)]">.lance</span> tables.
        Switching is immediate — the catalog is repointed in place, no restart. Everything
        the console does against a connection is read-only.
      </p>

      {state === null ? (
        <Empty>reading settings…</Empty>
      ) : (
        <>
          {state.connections.length === 0 && (
            <p className="text-[13px] text-[var(--haze)] mb-5">
              No connections saved yet.
            </p>
          )}

          <div className="space-y-2 mb-6">
            {state.connections.map((c) => (
              <div key={c.id}
                   className="flex items-start gap-4 px-4 py-3 rounded-sm border"
                   style={c.active
                     ? { borderColor: "var(--video)", background: "rgb(var(--video-rgb) / 0.09)" }
                     : { borderColor: "var(--rule)" }}>
                <div className="min-w-0 flex-1">
                  <div className="mono text-[13px]"
                       style={{ color: c.active ? "var(--video)" : "var(--bright)" }}>
                    {c.label}
                  </div>
                  <div className="mono text-[10px] text-[var(--haze)] truncate" title={c.uri}>
                    {c.uri}
                  </div>
                  <div className="mono text-[10px] mt-1 text-[var(--haze)]">
                    {c.reachable === false
                      ? <span style={{ color: "var(--video)" }}>unreachable — {c.note}</span>
                      : c.reachable === null
                        ? c.note
                        : `${c.tables.length} table${c.tables.length === 1 ? "" : "s"}`}
                    {c.last_used && ` · last used ${fmtWhen(c.last_used)}`}
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {!c.active && (
                    <button className="pill" disabled={busy || locked}
                            onClick={() => run(() => activateConnection(c.id))}>
                      Use
                    </button>
                  )}
                  <button className="pill" disabled={busy}
                          onClick={() => run(() => removeConnection(c.id))}>
                    Remove
                  </button>
                </div>
              </div>
            ))}
          </div>

          <div className="flex flex-wrap items-end gap-3">
            <Field label="path or URI" wide>
              <input
                className="inp mono" placeholder="/path/to/lance  ·  s3://bucket/prefix"
                value={uri}
                onChange={(e) => { setUri(e.target.value); setFound(null); }}
              />
            </Field>
            <Field label="name">
              <input className="inp mono" placeholder="optional" value={label}
                     onChange={(e) => setLabel(e.target.value)} />
            </Field>
            <button className="pill" disabled={!uri.trim() || busy}
                    onClick={async () => {
                      try { setFound(await probeConnection(uri)); onError(null); }
                      catch (e) { onError(e instanceof Error ? e.message : "probe failed"); }
                    }}>
              Check
            </button>
            <button className="pill" disabled={!uri.trim() || busy}
                    onClick={() => run(async () => {
                      const s = await addConnection(uri, label);
                      setUri(""); setLabel(""); setFound(null);
                      return s;
                    })}>
              Add & use
            </button>
          </div>

          {found && (
            <p className="mono text-[11px] mt-3"
               style={{ color: found.reachable === false ? "var(--video)" : "var(--index)" }}>
              {found.reachable === false
                ? found.note
                : found.reachable === null
                  ? found.note
                  : found.tables.length
                    ? `${found.tables.length} table(s): ${found.tables.join(", ")}`
                    : found.note}
            </p>
          )}

          {locked && (
            <Caveat>
              <span className="mono text-[var(--bright)]">LANCE_ROOT</span> is set in the
              environment, so it wins over anything saved here and the connection list is
              inert. Unset it and restart the API to manage connections from this page.
            </Caveat>
          )}
        </>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ intelligence */

const ROLE_HINT: Record<string, string> = {
  auto: "Use a key if one is set, else a local Ollama, else nothing.",
  anthropic: "Claude, through the Anthropic API. Needs a key.",
  ollama: "A model on this machine. No key, no spend, works offline.",
  "openai-compat": "Any OpenAI-shaped endpoint: OpenAI, Groq, vLLM, LM Studio.",
  none: "Off. The console keeps every deterministic surface it has.",
};

function Intelligence({ intel, probe, onProbe, onSaved, onError }: {
  intel: IntelligenceView | null;
  probe: IntelProbe | null;
  onProbe: () => void;
  onSaved: (i: IntelligenceView) => void;
  onError: (e: string | null) => void;
}) {
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [key, setKey] = useState("");
  const [saved, setSaved] = useState(false);

  const v = <T,>(field: string, fallback: T): T =>
    (draft[field] as T) ?? fallback;

  const set = (field: string, value: unknown) => {
    setDraft((d) => ({ ...d, [field]: value }));
    setSaved(false);
  };

  if (intel === null) {
    return <section className="panel p-6"><Eyebrow>Intelligence</Eyebrow><Empty>reading…</Empty></section>;
  }

  const provider = v("provider", intel.provider);

  return (
    <section className="panel p-6">
      <Eyebrow>Intelligence</Eyebrow>
      <p className="text-[13px] text-[var(--body)] leading-relaxed mb-5 max-w-[62ch]">
        Optional, and honest about it: the findings the console derives for itself cost
        nothing and need none of this. A provider adds the language layer on top — plain
        English filters, table summaries, and the ask box — and every response it produces
        reports the tokens and dollars it spent next to the bytes it read.
      </p>

      {probe && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-6">
          <Status
            on={probe.anthropic.key_set}
            title="Anthropic"
            detail={probe.anthropic.key_set
              ? `key from ${probe.anthropic.source} ${probe.anthropic.hint ?? ""}`
              : "no key — set ANTHROPIC_API_KEY, or paste one below"}
          />
          <Status
            on={probe.ollama.running}
            title="Ollama"
            detail={probe.ollama.running
              ? `${probe.ollama.models.length} model(s) at ${probe.ollama.host}`
              : `not running at ${probe.ollama.host}`}
          />
        </div>
      )}

      <div className="flex flex-wrap gap-3 mb-4">
        <Field label="provider">
          <select className="inp mono" value={provider}
                  onChange={(e) => set("provider", e.target.value)}>
            {intel.providers.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </Field>
        <Field label="model">
          {provider === "ollama" && probe?.ollama.models.length ? (
            <select className="inp mono" value={v("model", intel.model ?? "")}
                    onChange={(e) => set("model", e.target.value)}>
              <option value="">— pick —</option>
              {probe.ollama.models.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          ) : (
            <input className="inp mono" placeholder="claude-opus-5"
                   value={v("model", intel.model ?? "")}
                   onChange={(e) => set("model", e.target.value)} />
          )}
        </Field>
        <Field label="fast model">
          <input className="inp mono" placeholder="for NL → filter"
                 value={v("model_fast", intel.model_fast ?? "")}
                 onChange={(e) => set("model_fast", e.target.value)} />
        </Field>
        <Field label="spend ceiling (USD)">
          <input className="inp mono" placeholder="none" inputMode="decimal"
                 value={String(v("spend_ceiling_usd", intel.spend_ceiling_usd ?? ""))}
                 onChange={(e) => set("spend_ceiling_usd",
                   e.target.value === "" ? null : Number(e.target.value))} />
        </Field>
      </div>

      <p className="text-[12px] text-[var(--haze)] mb-5">{ROLE_HINT[provider]}</p>

      {provider === "ollama" && (
        <Field label="ollama host" wide>
          <input className="inp mono" placeholder="http://localhost:11434"
                 value={v("ollama_host", intel.ollama_host ?? "")}
                 onChange={(e) => set("ollama_host", e.target.value)} />
        </Field>
      )}

      {provider === "openai-compat" && (
        <Field label="base URL" wide>
          <input className="inp mono" placeholder="http://localhost:1234/v1"
                 value={v("base_url", intel.base_url ?? "")}
                 onChange={(e) => set("base_url", e.target.value)} />
        </Field>
      )}

      {provider !== "ollama" && provider !== "none" && (
        <div className="mt-4">
          <Field label="api key" wide>
            <input className="inp mono" type="password" autoComplete="off"
                   placeholder={intel.api_key_set
                     ? `set — ${intel.api_key_source} ${intel.api_key_hint ?? ""}`
                     : "paste to store on this machine, or leave blank and use the env var"}
                   value={key} onChange={(e) => { setKey(e.target.value); setSaved(false); }} />
          </Field>
          {intel.anthropic_key_in_env ? (
            <p className="text-[12px] text-[var(--haze)] mt-2">
              <span className="mono text-[var(--bright)]">ANTHROPIC_API_KEY</span> is in the
              environment and wins over anything stored here.
            </p>
          ) : (
            <Caveat>
              A key pasted here is written to{" "}
              <span className="mono text-[var(--bright)]">~/.config/lancescope/settings.json</span>{" "}
              in plain text, at mode 0600. That is a real tradeoff, not a formality — the
              environment variable is the safer path, and it always wins over this field.
            </Caveat>
          )}
        </div>
      )}

      <div className="flex items-center gap-3 mt-6">
        <button className="pill"
                onClick={async () => {
                  try {
                    const patch = { ...draft };
                    if (key) patch.api_key = key;
                    onSaved(await saveIntelligence(patch));
                    setDraft({}); setKey(""); setSaved(true); onError(null); onProbe();
                  } catch (e) {
                    onError(e instanceof Error ? e.message : "save failed");
                  }
                }}>
          Save
        </button>
        <button className="pill" onClick={onProbe}>Re-check providers</button>
        {saved && <span className="mono text-[11px]" style={{ color: "var(--index)" }}>saved</span>}
      </div>

      <p className="text-[12px] text-[var(--haze)] mt-5 leading-relaxed">{intel.active_note}</p>
    </section>
  );
}

function Status({ on, title, detail }: { on: boolean; title: string; detail: string }) {
  return (
    <div className="px-4 py-3 rounded-sm border"
         style={{ borderColor: on ? "rgb(var(--index-rgb) / 0.4)" : "var(--rule)" }}>
      <div className="mono text-[12px]" style={{ color: on ? "var(--index)" : "var(--haze)" }}>
        {on ? "●" : "○"} {title}
      </div>
      <div className="mono text-[10px] text-[var(--haze)] mt-1 break-all">{detail}</div>
    </div>
  );
}

/* ------------------------------------------------------------------------- where */

function Where({ state }: { state: SettingsState | null }) {
  if (!state) return null;
  const src: Record<string, string> = {
    env: "the LANCE_ROOT environment variable",
    connection: "the active connection",
    default: "the ingest pipeline's output directory, as a first-run fallback",
    none: "nothing — no connection is configured",
  };
  return (
    <section className="panel p-6">
      <Eyebrow>Where this came from</Eyebrow>
      <dl className="text-[13px] space-y-2">
        <Row k="reading">
          <span className="mono text-[var(--bright)]">{state.root.root ?? "—"}</span>
        </Row>
        <Row k="because of">{src[state.root.source]}</Row>
        {state.root.detail && <Row k="detail">{state.root.detail}</Row>}
        <Row k="settings file">
          <span className="mono">{state.settings_path}</span>
        </Row>
      </dl>
    </section>
  );
}

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-4">
      <dt className="eyebrow w-[110px] shrink-0 pt-0.5">{k}</dt>
      <dd className="text-[var(--body)] min-w-0 break-all">{children}</dd>
    </div>
  );
}

function Field({ label, wide = false, children }: {
  label: string; wide?: boolean; children: React.ReactNode;
}) {
  return (
    <label className={`block ${wide ? "w-full" : "w-[190px]"}`}>
      <span className="eyebrow block mb-1.5">{label}</span>
      {children}
    </label>
  );
}
