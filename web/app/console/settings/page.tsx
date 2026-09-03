"use client";

/** Settings — the one page in the console that writes anything.
 *
 *  Three things are configured or explained here: which database the console is
 *  reading, how the intelligence layer is powered, and where the root it landed on
 *  actually came from. They used to be four panels stacked down a single column,
 *  which meant the page was mostly scrolling past the two subjects you did not come
 *  for. They are tabs now — one subject on screen at a time, each addressable by
 *  `?tab=`, so a link can drop someone on the panel the sentence was about.
 *
 *  All three stay mounted. A half-typed URI or an unsaved API key surviving a tab
 *  switch is worth more than the DOM it costs. */

import { useRouter } from "next/navigation";
import { useEffect, useState, useSyncExternalStore } from "react";
import Icon, { type IconName } from "@/app/components/Icon";
import AppBar from "@/app/components/nav/AppBar";
import SampleDatasets from "@/app/components/samples/SampleDatasets";
import Spend from "@/app/components/settings/Spend";
import { Caveat, Empty, Eyebrow, fmtWhen } from "@/app/components/console/atoms";
import { dbParent } from "@/app/lib/dbname";
import { type RuntimeReport, getRuntime } from "@/app/lib/catalog";
import {
  type Capabilities, type IntelProbe, type IntelligenceView, type Probe,
  type SelfTest, type SettingsState,
  activateConnection, addConnection, getCapabilities, getSettings, probeConnection,
  probeIntelligence, removeConnection, runSelfTest, saveIntelligence,
} from "@/app/lib/settings";

/* ---------------------------------------------------------------------- tabs */

/** The three subjects, each with the one line that says why you would open it. */
const TABS = [
  {
    id: "connections",
    icon: "database",
    label: "connections",
    blurb: "Which database the console reads, and the ones it can read without a download.",
  },
  {
    id: "intelligence",
    icon: "spark",
    label: "intelligence",
    blurb: "The optional language layer — provider, models, key, and what it costs.",
  },
  {
    id: "provenance",
    icon: "system",
    label: "provenance",
    blurb: "Which rung set the root, where the settings file is, and what this Lance can do.",
  },
] as const satisfies readonly { id: string; icon: IconName; label: string; blurb: string }[];

type TabId = (typeof TABS)[number]["id"];

/** The URL is the tab, rather than a copy of it.
 *
 *  Holding the selection in `useState` and seeding it from `?tab=` inside an effect
 *  would render the wrong panel first and then correct itself; subscribing to the
 *  location renders the right one and lets the server snapshot stay honest about
 *  knowing nothing. `replaceState` fires no event of its own, so `pick` says so. */
const URL_CHANGED = "lancescope:tab";

function readTab(): TabId {
  const want = new URLSearchParams(window.location.search).get("tab");
  return TABS.some((t) => t.id === want) ? (want as TabId) : "connections";
}

function subscribeToTab(onChange: () => void) {
  window.addEventListener("popstate", onChange);
  window.addEventListener(URL_CHANGED, onChange);
  return () => {
    window.removeEventListener("popstate", onChange);
    window.removeEventListener(URL_CHANGED, onChange);
  };
}

export default function SettingsPage() {
  const [state, setState] = useState<SettingsState | null>(null);
  const [probe, setProbe] = useState<IntelProbe | null>(null);
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [runtime, setRuntime] = useState<RuntimeReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const tab = useSyncExternalStore(subscribeToTab, readTab, (): TabId => "connections");
  const router = useRouter();

  useEffect(() => {
    getSettings()
      .then(setState)
      .catch((e) => setError(e instanceof Error ? e.message : "settings unreachable"));
    probeIntelligence().then(setProbe).catch(() => setProbe(null));
    getCapabilities().then(setCaps).catch(() => setCaps(null));
    getRuntime().then(setRuntime).catch(() => setRuntime(null));
  }, []);

  /* A link can name a tab, but switching one is a view change rather than a
     navigation — replaceState keeps it out of the back button. */
  const pick = (id: TabId) => {
    const u = new URL(window.location.href);
    u.searchParams.set("tab", id);
    window.history.replaceState(null, "", u);
    window.dispatchEvent(new Event(URL_CHANGED));
  };

  const current = TABS.find((t) => t.id === tab) ?? TABS[0];
  // Null until /runtime answers. Defaulting to false means a slow answer shows the
  // controls for a moment rather than hiding them from the person who owns them.
  const kiosk = runtime?.kiosk ?? false;

  return (
    <main className="relative z-10 min-h-screen px-[var(--stage-pad)] pt-7 pb-16">
      <AppBar
        crumbs={[{ label: "Console", href: "/console" }, { label: "Settings" }]}
        showSettings={false}
      />

      {error && <Banner tone="video">{error}</Banner>}

      <div className="max-w-[1100px]">
        {/* Full width and even thirds on a phone, where three words at this
            tracking do not fit beside their glyphs; the glyphs go first. */}
        <div className="seg mb-3 w-full sm:w-auto" role="tablist"
             aria-label="Settings sections">
          {TABS.map((t) => (
            <button
              key={t.id}
              role="tab"
              id={`settings-tab-${t.id}`}
              aria-selected={tab === t.id}
              aria-controls={`settings-panel-${t.id}`}
              onClick={() => pick(t.id)}
              data-on={tab === t.id}
              className="mono flex-1 sm:flex-none !px-2 sm:!px-3.5
                         text-[10px] tracking-[0.14em] uppercase"
            >
              <span className="hidden sm:inline-flex"><Icon name={t.icon} size={14} /></span>
              {t.label}
            </button>
          ))}
        </div>

        {/* The strip is three words wide; this is the sentence those words stand
            for, and it changes with the tab rather than repeating in every panel. */}
        <p className="text-[12px] text-[var(--haze)] mb-6 leading-relaxed max-w-[70ch]">
          {current.blurb}
        </p>

        <div hidden={tab !== "connections"} role="tabpanel"
             id="settings-panel-connections" aria-labelledby="settings-tab-connections">
          <div className="space-y-6">
            <Connections state={state} onChange={setState} onError={setError}
                         kiosk={kiosk} />

            {/* Below the connection form, not above it: someone on this page usually
                came to point at their own database. The offer should be what they find
                next, rather than what they scroll past.

                One list, not two. There used to be a row of chips here filled from a
                hardcoded array; these cards say the same thing with measured numbers,
                a reason to open each one, and the sentence that actually decides it —
                that nothing is downloaded. */}
            <section className="panel p-6">
              <Eyebrow>Sample datasets</Eyebrow>
              <SampleDatasets onOpened={async () => {
                /* "Open it" adopted the URI and made it active. Staying here would
                   leave that button having visibly done nothing. */
                await getSettings().then(setState).catch(() => undefined);
                router.push("/console");
              }} />
            </section>
          </div>
        </div>

        <div hidden={tab !== "intelligence"} role="tabpanel"
             id="settings-panel-intelligence" aria-labelledby="settings-tab-intelligence">
          <div className="space-y-6">
            <Intelligence
              kiosk={kiosk}
              intel={state?.intelligence ?? null}
              probe={probe}
              caps={caps}
              onProbe={() => {
                probeIntelligence().then(setProbe).catch(() => setProbe(null));
                getCapabilities().then(setCaps).catch(() => setCaps(null));
              }}
              onSaved={(i) => setState((s) => (s ? { ...s, intelligence: i } : s))}
              onError={setError}
            />

            {/* Below the form that configures the key, because it is the answer to
                the question the form raises. Not on a kiosk: the routes it reads are
                not mounted there, and there is no key to have spent anything. */}
            {!kiosk && <Spend ceiling={state?.intelligence.spend_ceiling_usd ?? null} />}
          </div>
        </div>

        {/* Two panels that answer the same question from opposite ends — which
            database, and which reader — so they sit side by side once there is room. */}
        <div hidden={tab !== "provenance"} role="tabpanel"
             id="settings-panel-provenance" aria-labelledby="settings-tab-provenance">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
            <Where state={state} />
            <Reader runtime={runtime} />
          </div>
        </div>
      </div>
    </main>
  );
}

function Banner({ tone, children }: { tone: "video" | "index"; children: React.ReactNode }) {
  return (
    <div className="mono flex items-center gap-2.5 text-[12px] px-3.5 py-3 rounded-sm mb-6"
         style={{ background: `rgb(var(--${tone}-rgb) / 0.12)`,
                  border: `1px solid rgb(var(--${tone}-rgb) / 0.4)`,
                  color: `var(--${tone})` }}>
      <Icon name="warning" size={15} />
      {children}
    </div>
  );
}

/* ------------------------------------------------------------------- connections */

function Connections({ state, onChange, onError, kiosk = false }: {
  state: SettingsState | null;
  onChange: (s: SettingsState) => void;
  onError: (e: string | null) => void;
  kiosk?: boolean;
}) {
  const router = useRouter();
  const [uri, setUri] = useState("");
  const [label, setLabel] = useState("");
  const [found, setFound] = useState<Probe | null>(null);
  const [busy, setBusy] = useState(false);
  const [opening, setOpening] = useState<string | null>(null);

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

  /** The row is the way in.
   *
   *  Switching a connection and then looking at it were two clicks in two places —
   *  Use here, then the breadcrumb — and the first of them gave no evidence it had
   *  worked beyond a border changing colour. Opening a connection is what anyone
   *  came to this list to do, so the row does it: activate if it is not already
   *  active, then go and read it. `Use` stays for the narrower case of repointing
   *  the catalog without leaving this page. */
  const open = async (c: SettingsState["connections"][number]) => {
    if (!c.active) {
      if (locked) return;
      setOpening(c.id);
      try {
        onChange(await activateConnection(c.id));
        onError(null);
      } catch (e) {
        onError(e instanceof Error ? e.message : "failed");
        setOpening(null);
        return;
      }
    }
    router.push("/console");
  };

  return (
    <section className="panel p-6">
      <Eyebrow>Connections</Eyebrow>
      <p className="text-[13px] text-[var(--body)] leading-relaxed mb-5 max-w-[62ch]">
        Any directory holding <span className="mono text-[var(--bright)]">.lance</span> tables.
        Click one to open it in the console — switching is immediate, the catalog is
        repointed in place, no restart. Everything the console does against a connection
        is read-only.
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
                   className="group flex items-stretch rounded-sm border
                              focus-within:border-[var(--index)]"
                   style={c.active
                     ? { borderColor: "var(--video)", background: "rgb(var(--video-rgb) / 0.09)" }
                     : { borderColor: "var(--rule)" }}>
                <button
                  type="button"
                  disabled={busy || opening !== null || (locked && !c.active)}
                  onClick={() => open(c)}
                  title={locked && !c.active
                    ? "LANCE_ROOT is pinning the root — this cannot be made active"
                    : `Open ${c.label} in the console`}
                  className="flex items-start gap-4 flex-1 min-w-0 px-4 py-3 text-left
                             rounded-sm transition-colors
                             hover:bg-[rgb(var(--index-rgb)/0.11)]
                             disabled:hover:bg-transparent disabled:cursor-default"
                >
                <span className="pt-0.5 shrink-0"
                      style={{ color: c.active ? "var(--video)" : "var(--dim)" }}>
                  <Icon name={opening === c.id ? "clock" : c.active ? "check" : "database"}
                        size={16} />
                </span>
                <span className="min-w-0 flex-1 block">
                  <span className="block text-[14px] font-medium"
                        style={{ color: c.active ? "var(--video)" : "var(--bright)" }}>
                    {c.label}
                  </span>
                  <span className="mono block text-[10px] text-[var(--haze)] truncate"
                        title={c.uri}>
                    {dbParent(c.uri) || c.uri}
                  </span>
                  <span className="mono flex items-center gap-1.5 text-[10px] mt-1 text-[var(--haze)]">
                    {c.reachable === false
                      ? <span className="flex items-center gap-1.5" style={{ color: "var(--video)" }}>
                          <Icon name="warning" size={11} />unreachable — {c.note}
                        </span>
                      : c.reachable === null
                        ? <span className="flex items-center gap-1.5"
                                style={{ color: "var(--index)" }}>
                            <Icon name="info" size={11} />
                            remote — saved, not browsable
                          </span>
                        : <><Icon name="table" size={11} />
                            {c.tables.length} table{c.tables.length === 1 ? "" : "s"}</>}
                    {c.last_used && <span className="text-[var(--dim)]">· last used {fmtWhen(c.last_used)}</span>}
                  </span>
                  {c.capabilities?.remote && (
                    <span className="block text-[11px] text-[var(--haze)] leading-relaxed mt-1.5">
                      {c.capabilities.discover.reason}
                    </span>
                  )}
                </span>

                {/* The row goes somewhere, so it says so — quietly until it is
                    pointed at, because three permanent arrows down the list would
                    be louder than the one connection that is active. */}
                <span className="self-center shrink-0 pl-3 opacity-0 transition-opacity
                                 group-hover:opacity-100 group-focus-within:opacity-100"
                      style={{ color: c.active ? "var(--video)" : "var(--index)" }}>
                  <Icon name="chevronRight" size={16} />
                </span>
                </button>

                <span className="flex items-center gap-2 shrink-0 pr-3">
                  {!c.active && (
                    <button className="btn" disabled={busy || opening !== null || locked}
                            data-tip="Switch the catalog without leaving this page"
                            data-tip-side="left"
                            onClick={() => run(() => activateConnection(c.id))}>
                      <Icon name="check" size={14} />
                      Use
                    </button>
                  )}
                  <button className="iconbtn" disabled={busy || opening !== null}
                          data-tip="Remove" data-tip-side="left"
                          aria-label={`Remove ${c.label}`}
                          onClick={() => run(() => removeConnection(c.id))}>
                    <Icon name="trash" size={14} />
                  </button>
                </span>
              </div>
            ))}
          </div>

          <div className="flex flex-wrap items-end gap-3">
            <Field label="path or URI" wide className="max-w-[520px] min-w-[260px] flex-1">
              <input
                className="inp mono"
                placeholder="/path/to/lance  ·  hf://datasets/…  ·  s3://bucket/prefix"
                value={uri}
                onChange={(e) => { setUri(e.target.value); setFound(null); }}
              />
            </Field>
            <Field label="name">
              <input className="inp mono" placeholder="optional" value={label}
                     onChange={(e) => setLabel(e.target.value)} />
            </Field>
            <button className="btn" disabled={!uri.trim() || busy || locked || kiosk}
                    onClick={async () => {
                      try { setFound(await probeConnection(uri)); onError(null); }
                      catch (e) { onError(e instanceof Error ? e.message : "probe failed"); }
                    }}>
              <Icon name="search" size={14} />
              Check
            </button>
            {/* Disabled where the server would refuse. `locked` is LANCE_ROOT,
                which saves the connection but cannot switch to it; `kiosk` is the
                public demo, which refuses outright. Both were previously live
                buttons that appeared to work. */}
            <button className="btn btn-accent"
                    disabled={!uri.trim() || busy || locked || kiosk}
                    onClick={() => run(async () => {
                      const s = await addConnection(uri, label);
                      setUri(""); setLabel(""); setFound(null);
                      return s;
                    })}>
              <Icon name="plus" size={14} />
              Add &amp; use
            </button>
          </div>

          {found && (
            <p className="mono flex items-center gap-2 text-[11px] mt-3"
               style={{ color: found.reachable === false ? "var(--video)" : "var(--index)" }}>
              <Icon name={found.reachable === false ? "warning" : found.reachable === null ? "info" : "check"} size={13} />
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

function Intelligence({ intel, probe, caps, onProbe, onSaved, onError, kiosk = false }: {
  intel: IntelligenceView | null;
  probe: IntelProbe | null;
  caps: Capabilities | null;
  onProbe: () => void;
  onSaved: (i: IntelligenceView) => void;
  onError: (e: string | null) => void;
  kiosk?: boolean;
}) {
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [key, setKey] = useState("");
  const [saved, setSaved] = useState(false);
  const [test, setTest] = useState<SelfTest | null>(null);
  const [testing, setTesting] = useState(false);

  const v = <T,>(field: string, fallback: T): T =>
    (draft[field] as T) ?? fallback;

  const set = (field: string, value: unknown) => {
    setDraft((d) => ({ ...d, [field]: value }));
    setSaved(false);
  };

  // Before the reading-state check, because on a kiosk the panel is never going to
  // arrive: the router that answers these routes is not mounted at all. A form that
  // renders and then 404s on save would be worse than a sentence.
  if (kiosk) {
    return (
      <section className="panel p-6">
        <Eyebrow>Intelligence</Eyebrow>
        <p className="text-[13px] text-[var(--body)] leading-relaxed max-w-[62ch]">
          Off on the public demo, and not configurable here. The language layer bills a
          provider per request, so a shared console does not carry one — which costs
          this demo nothing it exists to show: every finding, every byte count and every
          access path on the other tabs is derived from metadata, with no model involved.
        </p>
        <p className="text-[13px] text-[var(--body)] leading-relaxed max-w-[62ch] mt-4">
          <a href="https://lancescope.mlynn.dev/download"
             className="underline underline-offset-2"
             style={{ color: "var(--video)" }}>
            Run LanceScope on your own database
          </a>{" "}
          to point it at Claude, or at a model on your own machine.
        </p>
      </section>
    );
  }

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

      {caps && (
        <div className="px-4 py-3 rounded-sm border mb-4"
             style={{ borderColor: caps.available
                        ? "rgb(var(--index-rgb) / 0.4)" : "var(--rule)",
                      background: caps.available
                        ? "rgb(var(--index-rgb) / 0.05)" : "transparent" }}>
          <div className="mono text-[12px] flex items-center gap-2"
               style={{ color: caps.available ? "var(--index)" : "var(--haze)" }}>
            <Icon name={caps.available ? "check" : "info"} size={14} />
            {caps.available
              ? `${caps.models_by_role.deep.id} via ${caps.provider}`
              : "no provider"}
          </div>
          <div className="mono text-[10px] text-[var(--haze)] mt-1">
            {caps.reason}
            {caps.available && caps.models_by_role.fast.id !== caps.models_by_role.deep.id
              && ` · translation on ${caps.models_by_role.fast.id}`}
          </div>
          {caps.available && !caps.tools_capable && (
            <div className="mono text-[10px] text-[var(--haze)] mt-1">
              Summaries and filters yes; the ask box needs a tool-capable model.
            </div>
          )}
          {!caps.available && caps.setup_hint && (
            <div className="mono text-[10px] text-[var(--haze)] mt-1">{caps.setup_hint}</div>
          )}
        </div>
      )}

      {probe && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-6">
          <Status
            icon="spark"
            on={probe.anthropic.key_set}
            title="Anthropic"
            detail={probe.anthropic.key_set
              ? `key from ${probe.anthropic.source} ${probe.anthropic.hint ?? ""}`
              : "no key — set ANTHROPIC_API_KEY, or paste one below"}
          />
          <Status
            icon="system"
            on={probe.ollama.running}
            title="Ollama"
            detail={probe.ollama.running
              ? `${probe.ollama.models.length} model(s) at ${probe.ollama.host}`
              : `not running at ${probe.ollama.host}`}
          />
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-x-4 gap-y-3 mb-4">
        <Field label="provider" wide>
          <select className="inp mono" value={provider}
                  onChange={(e) => set("provider", e.target.value)}>
            {intel.providers.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </Field>
        <Field label="model" wide>
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
        <Field label="fast model" wide>
          <input className="inp mono" placeholder="for NL → filter"
                 value={v("model_fast", intel.model_fast ?? "")}
                 onChange={(e) => set("model_fast", e.target.value)} />
        </Field>
        <Field label="spend ceiling (USD)" wide>
          <input className="inp mono" placeholder="none" inputMode="decimal"
                 value={String(v("spend_ceiling_usd", intel.spend_ceiling_usd ?? ""))}
                 onChange={(e) => set("spend_ceiling_usd",
                   e.target.value === "" ? null : Number(e.target.value))} />
        </Field>
      </div>

      <p className="text-[12px] text-[var(--haze)] mb-5">{ROLE_HINT[provider]}</p>

      {provider === "ollama" && (
        <Field label="ollama host" wide className="max-w-[520px]">
          <input className="inp mono" placeholder="http://localhost:11434"
                 value={v("ollama_host", intel.ollama_host ?? "")}
                 onChange={(e) => set("ollama_host", e.target.value)} />
        </Field>
      )}

      {provider === "openai-compat" && (
        <Field label="base URL" wide className="max-w-[520px]">
          <input className="inp mono" placeholder="http://localhost:1234/v1"
                 value={v("base_url", intel.base_url ?? "")}
                 onChange={(e) => set("base_url", e.target.value)} />
        </Field>
      )}

      {provider !== "ollama" && provider !== "none" && (
        <div className="mt-4">
          <Field label="api key" wide className="max-w-[520px]">
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
        <button className="btn btn-accent"
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
          <Icon name="check" size={14} />
          Save
        </button>
        <button className="btn" onClick={onProbe}>
          <Icon name="refresh" size={14} />
          Re-check providers
        </button>
        <button className="btn" disabled={testing}
                onClick={async () => {
                  setTesting(true); setTest(null);
                  try {
                    setTest(await runSelfTest("fast"));
                  } catch (e) {
                    onError(e instanceof Error ? e.message : "self-test failed");
                  } finally {
                    setTesting(false);
                  }
                }}>
          <Icon name="spark" size={14} />
          {testing ? "asking…" : "Test the model"}
        </button>
        {saved && (
          <span className="mono flex items-center gap-1.5 text-[11px]" style={{ color: "var(--index)" }}>
            <Icon name="check" size={13} />saved
          </span>
        )}
      </div>

      {testing && (
        <p className="mono text-[11px] text-[var(--haze)] mt-4">
          One real call to the configured model. A large local model can take half a
          minute, and longer if it has to load first.
        </p>
      )}

      {test && (
        <div className="mt-4 px-4 py-3 rounded-sm border"
             style={{ borderColor: test.ok
                        ? "rgb(var(--index-rgb) / 0.4)" : "rgb(var(--video-rgb) / 0.4)",
                      background: test.ok
                        ? "rgb(var(--index-rgb) / 0.05)" : "rgb(var(--video-rgb) / 0.07)" }}>
          <div className="mono text-[12px] flex items-center gap-2"
               style={{ color: test.ok ? "var(--index)" : "var(--video)" }}>
            <Icon name={test.ok ? "check" : "warning"} size={14} />
            {test.ok ? "answered, and honoured the schema" : (test.error ?? "failed")}
          </div>
          {test.ok && (
            <>
              <p className="text-[12px] text-[var(--body)] mt-2 leading-relaxed">
                {String(test.data?.answer ?? test.text ?? "")}
              </p>
              <div className="mono text-[10px] text-[var(--haze)] mt-2">
                {test.model} · {((test.ms ?? 0) / 1000).toFixed(1)}s ·{" "}
                {test.usage?.input_tokens ?? 0} in / {test.usage?.output_tokens ?? 0} out ·{" "}
                {test.cost_usd === 0
                  ? "no cost — this ran on your machine"
                  : test.cost_usd == null
                    ? "cost unknown — this model is not in the price registry"
                    : `$${test.cost_usd.toFixed(5)}`}
              </div>
            </>
          )}
          {!test.ok && test.setup_hint && (
            <div className="mono text-[10px] text-[var(--haze)] mt-2">{test.setup_hint}</div>
          )}
        </div>
      )}

      <p className="text-[12px] text-[var(--haze)] mt-5 leading-relaxed">{intel.active_note}</p>
    </section>
  );
}

function Status({ on, title, detail, icon }: {
  on: boolean; title: string; detail: string; icon: IconName;
}) {
  return (
    <div className="flex items-start gap-3 px-4 py-3 rounded-sm border"
         style={{ borderColor: on ? "rgb(var(--index-rgb) / 0.4)" : "var(--rule)",
                  background: on ? "rgb(var(--index-rgb) / 0.05)" : "transparent" }}>
      <span className="pt-0.5" style={{ color: on ? "var(--index)" : "var(--dim)" }}>
        <Icon name={icon} size={16} />
      </span>
      <div className="min-w-0">
        <div className="text-[13px] flex items-center gap-2"
             style={{ color: on ? "var(--index)" : "var(--haze)" }}>
          {title}
          <span className="mono text-[9px] tracking-[0.14em] uppercase px-1.5 py-0.5 rounded-sm border"
                style={{ borderColor: "currentColor" }}>
            {on ? "available" : "off"}
          </span>
        </div>
        <div className="mono text-[10px] text-[var(--haze)] mt-1 break-all">{detail}</div>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------- provenance */

/** Why this panel exists.
 *
 *  The root can be set in four places and only one of them wins. When the console
 *  is showing you a database you did not expect — someone else's `LANCE_ROOT`, a
 *  first-run fallback into the demo corpus, a connection you forgot you activated
 *  — this is the panel that tells you which rung you are standing on and why the
 *  ones above it did not fire. It used to be a bare `dl` headed "Where this came
 *  from", which was a fair description of the data and no description at all of
 *  the question it answers.
 *
 *  The settings file path is here for the same reason: it is the file every button
 *  on this page writes, and knowing where it is turns "the console has the wrong
 *  connection saved" into something you can go and look at.
 */

const RUNGS: { source: string; icon: IconName; name: string; what: string }[] = [
  {
    source: "env",
    icon: "system",
    name: "LANCE_ROOT",
    what: "An environment variable on the API process. Set, it wins over everything below and this page cannot override it.",
  },
  {
    source: "connection",
    icon: "database",
    name: "The active connection",
    what: "Whichever saved connection is marked Use, on the Connections tab. This is the normal case.",
  },
  {
    source: "default",
    icon: "play",
    name: "data/lance",
    what: "The ingest pipeline's output, used on a first run with nothing configured — and only if it actually holds tables.",
  },
  {
    source: "none",
    icon: "warning",
    name: "Nothing",
    what: "No root resolved. The console has nothing to read until a connection is added.",
  },
];

/** Which Lance is doing the reading, and what this build of it can do.
 *
 *  Here rather than in a panel because it is a property of the install, not of the
 *  table being looked at — and because the question it answers ("why is there no
 *  cost column?") is asked on a screen that is already missing the thing that would
 *  have explained it. A build with everything says so in one line and takes up no
 *  more room than that; only a degraded one spends space on the detail. */
function Reader({ runtime }: { runtime: RuntimeReport | null }) {
  if (!runtime) return null;
  const missing = runtime.features.filter((f) => !f.supported);

  return (
    <section className="panel p-6">
      <Eyebrow>The reader</Eyebrow>

      <div className="flex flex-wrap gap-x-6 gap-y-2 mb-4">
        {([["lance", "pylance"], ["pyarrow", "pyarrow"],
           ["python", "python"]] as const).map(([key, label]) => (
          <div key={key}>
            <div className="eyebrow mb-0.5">{label}</div>
            <div className="mono text-[13px] text-[var(--bright)]">
              {runtime.versions[key]}
            </div>
          </div>
        ))}
      </div>

      {missing.length === 0 ? (
        <p className="text-[13px] text-[var(--body)] leading-relaxed max-w-[62ch]">
          This Lance supports everything the console asks of it — cost accounting,
          Blob V2, indices, versions and fragment statistics.
        </p>
      ) : (
        <>
          <p className="text-[13px] leading-relaxed max-w-[62ch] mb-4"
             style={{ color: "var(--video)" }}>
            {runtime.summary}
          </p>
          <ul className="space-y-2.5">
            {missing.map((f) => (
              <li key={f.name} className="flex items-start gap-3 px-3.5 py-2.5 rounded-sm
                                          border" style={{ borderColor: "var(--rule)" }}>
                <span className="pt-0.5 shrink-0" style={{ color: "var(--video)" }}>
                  <Icon name="warning" size={15} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[13px] text-[var(--bright)]">{f.name}</span>
                  <span className="block text-[12px] text-[var(--haze)] mt-1 leading-relaxed">
                    {f.lost}
                  </span>
                  <span className="block mono text-[11px] text-[var(--dim)] mt-1.5 break-all">
                    {f.probe}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function Where({ state }: { state: SettingsState | null }) {
  if (!state) return null;
  const activeRung = state.root.source;

  return (
    <section className="panel p-6">
      <Eyebrow>Where the console is reading from</Eyebrow>
      <p className="text-[13px] text-[var(--body)] leading-relaxed mb-5 max-w-[62ch]">
        Four things can set the root and exactly one of them wins. If the console is
        pointed somewhere you did not expect, this says which rung it landed on.
      </p>

      <div className="flex items-start gap-3 px-4 py-3.5 rounded-sm mb-5"
           style={{ background: "rgb(var(--video-rgb) / 0.08)",
                    border: "1px solid rgb(var(--video-rgb) / 0.35)" }}>
        <span className="pt-0.5" style={{ color: "var(--video)" }}>
          <Icon name="database" size={17} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="eyebrow mb-1">now reading</div>
          <div className="mono text-[13px] text-[var(--bright)] break-all">
            {state.root.root ?? "— nothing —"}
          </div>
          {state.root.detail && (
            <div className="text-[12px] text-[var(--haze)] mt-1.5 leading-relaxed">
              {state.root.detail}
            </div>
          )}
        </div>
        {state.root.root && <Copy value={state.root.root} what="path" />}
      </div>

      <ol className="space-y-1.5 mb-6">
        {RUNGS.map((r, i) => {
          const on = r.source === activeRung;
          return (
            <li key={r.source}
                className="flex items-start gap-3 px-3.5 py-2.5 rounded-sm border"
                style={on
                  ? { borderColor: "rgb(var(--index-rgb) / 0.5)",
                      background: "rgb(var(--index-rgb) / 0.06)" }
                  : { borderColor: "var(--rule)", opacity: 0.62 }}>
              <span className="mono text-[10px] pt-1 w-3 shrink-0"
                    style={{ color: on ? "var(--index)" : "var(--dim)" }}>
                {i + 1}
              </span>
              <span className="pt-0.5" style={{ color: on ? "var(--index)" : "var(--dim)" }}>
                <Icon name={r.icon} size={15} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-2 text-[13px]"
                      style={{ color: on ? "var(--index)" : "var(--haze)" }}>
                  <span className="mono">{r.name}</span>
                  {on && (
                    <span className="mono text-[9px] tracking-[0.14em] uppercase px-1.5 py-0.5
                                     rounded-sm border" style={{ borderColor: "currentColor" }}>
                      in effect
                    </span>
                  )}
                </span>
                <span className="block text-[12px] text-[var(--haze)] mt-1 leading-relaxed">
                  {r.what}
                </span>
              </span>
            </li>
          );
        })}
      </ol>

      <div className="flex items-start gap-3 pt-5"
           style={{ borderTop: "1px solid var(--rule)" }}>
        <span className="pt-0.5 text-[var(--dim)]"><Icon name="settings" size={15} /></span>
        <div className="min-w-0 flex-1">
          <div className="eyebrow mb-1">settings file</div>
          <div className="mono text-[11px] text-[var(--body)] break-all">{state.settings_path}</div>
          <p className="text-[12px] text-[var(--haze)] mt-1.5 leading-relaxed max-w-[62ch]">
            Every button on this page writes here and nowhere else — the connection list,
            the active one, and the intelligence settings. Written at mode 0600, because a
            stored API key would be in it. Move it with{" "}
            <span className="mono text-[var(--bright)]">LANCESCOPE_CONFIG</span>.
          </p>
        </div>
        <Copy value={state.settings_path} what="path" />
      </div>
    </section>
  );
}

/** Copy-to-clipboard for the two paths on this page that people actually need to
 *  paste somewhere else. */
function Copy({ value, what }: { value: string; what: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      className="iconbtn shrink-0"
      data-tip={done ? "Copied" : `Copy ${what}`}
      data-tip-side="left"
      aria-label={`Copy ${what}`}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setDone(true);
          setTimeout(() => setDone(false), 1400);
        } catch {
          // No clipboard permission; the path is on screen and selectable anyway.
        }
      }}
    >
      <Icon name={done ? "check" : "external"} size={14} />
    </button>
  );
}

function Field({ label, wide = false, className = "", children }: {
  label: string; wide?: boolean; className?: string; children: React.ReactNode;
}) {
  return (
    <label className={`block ${wide ? "w-full" : "w-[190px]"} ${className}`}>
      <span className="eyebrow block mb-1.5">{label}</span>
      {children}
    </label>
  );
}
