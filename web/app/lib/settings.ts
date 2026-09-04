// Client for /settings. Shapes mirror server/routes/settings.py.
//
// The console is read-only against data; this is the one surface that writes, and
// what it writes is the settings file — never a dataset.

export type ResolvedRoot = {
  root: string | null;
  source: "env" | "connection" | "default" | "none";
  connection_id: string | null;
  detail: string;
};

export type ConnectionView = {
  id: string;
  label: string;
  uri: string;
  added: string;
  last_used: string | null;
  active: boolean;
  reachable: boolean | null;
  tables: string[];
  note: string;
  capabilities?: import("@/app/lib/catalog").RootCapabilities;
};

export type IntelligenceView = {
  enabled: boolean;
  provider: string;
  model: string | null;
  model_fast: string | null;
  ollama_host: string | null;
  base_url: string | null;
  api_key: null;
  spend_ceiling_usd: number | null;
  cache_dir: string | null;
  api_key_set: boolean;
  api_key_source: "env" | "settings" | null;
  api_key_hint: string | null;
  anthropic_key_in_env: boolean;
  providers: string[];
  active: boolean;
  active_note: string;
};

export type SettingsState = {
  settings_path: string;
  root: ResolvedRoot;
  env_locked: boolean;
  connections: ConnectionView[];
  intelligence: IntelligenceView;
};

export type Probe = {
  uri: string;
  reachable: boolean | null;
  tables: string[];
  note: string;
  capabilities?: import("@/app/lib/catalog").RootCapabilities;
};

export type IntelProbe = {
  anthropic: { key_set: boolean; source: string | null; hint: string | null };
  ollama: { host: string; running: boolean; models: string[]; error: string | null };
  openai_compat: { base_url: string | null; key_set: boolean };
  any_provider_available: boolean;
};

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/settings${path}`, {
    cache: "no-store",
    headers: init?.body ? { "content-type": "application/json" } : undefined,
    ...init,
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(String(detail));
  }
  return res.json();
}

export const getSettings = () => call<SettingsState>("");

export const probeConnection = (uri: string) =>
  call<Probe>("/connections/probe", { method: "POST", body: JSON.stringify({ uri }) });

export const addConnection = (uri: string, label: string) =>
  call<SettingsState>("/connections", {
    method: "POST",
    body: JSON.stringify({ uri, label, activate: true }),
  });

export const activateConnection = (id: string) =>
  call<SettingsState>(`/connections/${id}/activate`, { method: "POST" });

export const removeConnection = (id: string) =>
  call<SettingsState>(`/connections/${id}`, { method: "DELETE" });

export const saveIntelligence = (patch: Record<string, unknown>) =>
  call<IntelligenceView>("/intelligence", { method: "PUT", body: JSON.stringify(patch) });

export const probeIntelligence = () => call<IntelProbe>("/intelligence/probe");

/* ------------------------------------------------------------------ intelligence */

export type ModelInfo = {
  id: string;
  provider: string;
  context: number | null;
  input_usd_per_mtok: number | null;
  output_usd_per_mtok: number | null;
  structured_output: boolean;
  tools: boolean;
  priced_on: string | null;
  note: string;
};

export type Capabilities = {
  available: boolean;
  provider: string;
  reason: string;
  models_by_role: { deep: ModelInfo; fast: ModelInfo };
  tools_capable: boolean;
  key_source: string | null;
  host: string | null;
  setup_hint: string;
  priced_on: string;
};

/** A self-test answers 200 whether or not it worked — a provider that fails is a
 *  result, not a server fault. Read `ok`, never the status code. */
export type SelfTest = {
  ok: boolean;
  role: string;
  provider?: string;
  model?: string | null;
  error: string | null;
  setup_hint?: string;
  retryable?: boolean;
  text?: string;
  data?: Record<string, unknown> | null;
  usage?: { input_tokens: number; output_tokens: number; cache_read_tokens: number };
  cost_usd?: number | null;
  ms?: number;
};

async function intel<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/intel${path}`, { cache: "no-store", ...init });
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}

export const getCapabilities = () => intel<Capabilities>("/capabilities");

/** One suggestion in the model picker, carrying where it came from.
 *
 *  `source` is the honest part: `registry` is a model we ship a price for,
 *  `installed` is pulled onto this machine, `endpoint` is one the server named when
 *  asked. Only the first two can be shown with a price. */
export type ModelOption = {
  id: string;
  source: "registry" | "installed" | "endpoint";
  context: number | null;
  input_usd_per_mtok: number | null;
  output_usd_per_mtok: number | null;
  priced: boolean;
  priced_on: string | null;
  tools: boolean;
  note: string;
  recommended_for: string[];
};

/** What a provider could be asked for — never what it may be limited to.
 *
 *  `free_text` is true for every provider, and the picker honours it: the list is
 *  the help, and a model nobody here has heard of still has to be typeable. */
export type ProviderModels = {
  provider: string;
  options: ModelOption[];
  reachable: boolean;
  reason: string;
  free_text: boolean;
  host: string | null;
  priced_on: string;
};

/** `host` and `base_url` ask about the endpoint currently in the form, not the one
 *  last saved — so a picker is useful before the Save button has been pressed. */
export const getModels = (provider = "auto", host?: string, baseUrl?: string) => {
  const q = new URLSearchParams({ provider });
  if (host) q.set("host", host);
  if (baseUrl) q.set("base_url", baseUrl);
  return intel<ProviderModels>(`/models?${q}`);
};

export const runSelfTest = (role = "fast") =>
  intel<SelfTest>(`/selftest?role=${role}`, { method: "POST" });

/* --------------------------------------------------------------- nl -> filter */

/** A translation is a draft: the predicate is returned for the user to read, edit
 *  and run — never applied for them. `matched_rows` is the evidence that says
 *  whether it understood the question, and it costs one metadata read. */
export type FilterDraft = {
  ok: boolean;
  question?: string;
  filter?: string;
  explanation?: string;
  confidence?: "high" | "medium" | "low" | "refuse";
  valid?: boolean | null;
  matched_rows?: number | null;
  total_rows?: number | null;
  error?: string | null;
  setup_hint?: string;
  columns?: string[];
  faceted_columns?: string[];
  values_included?: boolean;
  context_read_bytes?: number;
  dry_run_read_bytes?: number;
  model?: string;
  provider?: string;
  cost_usd?: number | null;
  ms?: number;
  usage?: { input_tokens: number; output_tokens: number; cache_read_tokens: number };
};

export const askForFilter = (table: string, question: string, includeValues?: boolean) =>
  intel<FilterDraft>(`/tables/${table}/filter`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question, include_values: includeValues ?? null }),
  });

/* ------------------------------------------------------------------- summaries */

/** A description of a table, written from its schema, statistics and findings —
 *  never its rows. Cached against the dataset version: Lance versions are
 *  immutable, so an answer about one stays true about it. */
export type TableSummary = {
  ok: boolean;
  cached: boolean;
  summary?: string;
  most_notable?: string;
  version?: number;
  partial_analysis?: boolean;
  provider?: string;
  model?: string;
  cost_usd?: number | null;
  ms?: number;
  context_read_bytes?: number;
  usage?: { input_tokens: number; output_tokens: number; cache_read_tokens: number };
  error?: string;
  setup_hint?: string;
};

export const summariseTable = (table: string, refresh = false) =>
  intel<TableSummary>(`/tables/${table}/summary`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ refresh }),
  });

/* ----------------------------------------------------------------- the meter */

/** Tokens and dollars this server process has spent. Beside the byte meter,
 *  because a tool built to make read cost visible should not hide inference cost. */
export type TokenMeter = {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cost_usd: number;
  calls: number;
  cache_hits: number;
  unpriced_calls: number;
  ceiling_usd: number | null;
  seconds: number;
};

export const getTokenMeter = () => intel<TokenMeter>("/meter");

export const resetTokenMeter = () =>
  intel<TokenMeter>("/meter/reset", { method: "POST" });

/* ------------------------------------------------------------- the spend ledger */

/** One rollup — of a day, a task, a model, or the whole window.
 *
 *  `cost_usd` and `avoided_usd` are kept apart on purpose. A cache hit saved money;
 *  folding the saving into the spend would make the cache look expensive and the
 *  bill look larger, in the same number. */
export type SpendBucket = {
  calls: number;
  cache_hits: number;
  cost_usd: number;
  avoided_usd: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  unpriced_calls: number;
  avg_ms: number;
  /** Present on a day: the same rollup again, split by which task spent it. */
  tasks?: Record<string, SpendBucket>;
  day?: string;
  task?: string;
  model?: string;
  provider?: string;
};

/** One line of the ledger: what a single call cost, and nothing about what it was
 *  for beyond which task asked for it. */
export type SpendEvent = {
  ts: number;
  task: string;
  provider: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cost_usd: number | null;
  ms: number;
  cached: boolean;
  avoided_usd: number | null;
};

export type SpendHistory = {
  window_days: number;
  /** Every day in the window, oldest first — including the ones nothing happened. */
  daily: SpendBucket[];
  by_task: SpendBucket[];
  by_model: SpendBucket[];
  totals: SpendBucket;
  recent: SpendEvent[];
  first_ts: number | null;
  session: TokenMeter;
  ceiling_usd: number | null;
  provider: string;
  logging: boolean;
  ledger_path: string;
  rates: { priced_on: string; models: ModelInfo[] };
};

export const getSpend = (days = 30) => intel<SpendHistory>(`/spend?days=${days}`);

export const clearSpend = () => intel<{ cleared: boolean }>("/spend", { method: "DELETE" });

// ------------------------------------------------------------ sample datasets

/** A public Lance dataset offered to a console with nothing in it yet.
 *
 *  Nothing is installed and nothing is downloaded: opening one saves a URI, and
 *  pylance reads `hf://` lazily, so the bytes that move are the bytes you look at. */
export type Sample = {
  slug: string;
  uri: string;
  title: string;
  /** What the data is. */
  what: string;
  /** Why it is worth opening in *this* console specifically. */
  shows: string;
  /** Measured, not estimated. */
  scale: string;
  tables: number;
  first: string;
  added: boolean;
};

export type SampleList = { samples: Sample[]; note: string };

export async function getSamples(): Promise<SampleList> {
  const res = await fetch("/api/settings/samples", { cache: "no-store" });
  if (!res.ok) throw new Error(`samples: ${res.status}`);
  return res.json();
}

/** What happened when a sample was opened. `adopted` is false when `LANCE_ROOT`
 *  pins the console elsewhere — the connection is still saved, and `note` says so. */
export type SampleOpened = { adopted: boolean; note: string };

export async function openSample(uri: string): Promise<SampleOpened> {
  const res = await fetch("/api/settings/samples/open", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ uri }),
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try { detail = (await res.json()).detail ?? detail; } catch { }
    throw new Error(String(detail));
  }
  return res.json();
}
