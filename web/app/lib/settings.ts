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
