/** Operation plans, and the assistant that proposes them.
 *
 *  Two surfaces, one file, because they are one feature: the assistant's answer to
 *  "what should I do about this table" is a plan, and the plan is what the console
 *  renders. Splitting them would put the type for `propose_operation`'s result
 *  somewhere the panel that draws it does not import.
 *
 *  Nothing here runs anything. `POST /ops/tables/{t}/plan` computes a document and
 *  `script` is text — the copy button is the whole execution path, and it ends at
 *  the user's own terminal. */

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, { cache: "no-store", ...init });
  if (!res.ok) {
    // The server's own sentence, not "500". Every refusal under /ops carries one —
    // a plan for an operation this table cannot take is an explanation, and losing
    // it here would turn the useful half into a status code.
    let detail = `${res.status}`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch { /* a body that is not JSON is still a failure worth reporting */ }
    throw new Error(detail);
  }
  return res.json();
}

/* -------------------------------------------------------------------- plans */

export type Capability = { state: string; reason: string; available: boolean };

export type Precondition = { claim: string; holds: boolean; detail: string };

export type Estimate = {
  /** `null` means not modelled — never zero. A scalar index's on-disk size depends
   *  on cardinality the console has not read, and printing 0 B would be a claim. */
  read_bytes: number | null;
  write_bytes: number | null;
  disk_delta_bytes: number | null;
  seconds: number | null;
  basis: string;
};

export type PlanSummary = {
  id: string;
  kind: string;
  table: string;
  title: string;
  summary: string;
  ready: boolean;
  reversible: boolean;
  capability: Capability;
  estimate: Estimate;
  affected: Record<string, unknown>;
  caveats: string[];
  blocked_by: Precondition[];
};

export type OperationPlan = {
  id: string;
  kind: string;
  table: string;
  uri: string;
  target_version: number;
  title: string;
  summary: string;
  capability: Capability;
  ready: boolean;
  preconditions: Precondition[];
  affected: Record<string, unknown>;
  estimate: Estimate;
  reversible: boolean;
  rollback: string;
  verification: string;
  caveats: string[];
  script: string;
  generated_at: string;
  read_bytes: number;
  read_iops: number;
  executed: false;
  how_to_run: string;
  stale?: boolean;
  current_version?: number;
};

export type Proposals = {
  table: string;
  version: number;
  proposals: PlanSummary[];
  read_bytes: number;
  read_iops: number;
};

export const KINDS = [
  "index", "compact", "cleanup", "restore",
  "migrate-format", "migrate-copy", "migrate-schema", "migrate-embed",
] as const;

export type Kind = (typeof KINDS)[number];

export const getProposals = (table: string) =>
  api<Proposals>(`/ops/tables/${table}/proposals`);

export const buildPlan = (table: string, body: { kind: Kind } & Record<string, unknown>) =>
  api<OperationPlan>(`/ops/tables/${table}/plan`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });

/* ---------------------------------------------------------------- the assistant */

export type TraceStep = {
  tool: string;
  arguments: Record<string, unknown>;
  read_bytes: number;
  read_iops: number;
  ms: number;
  error: string;
};

/** Why a run ended. `answered` is the only one that means the model was finished —
 *  the rest are budgets, and a UI that rendered them all as "done" would be hiding
 *  the difference between an answer and half of one. */
export type Stop =
  | "answered" | "turn-limit" | "byte-budget" | "spend-budget"
  | "timeout" | "provider-error";

export type Answer = {
  ok: boolean;
  answer: string;
  stop: Stop;
  detail: string;
  complete: boolean;
  trace: TraceStep[];
  turns: number;
  read_bytes: number;
  read_iops: number;
  usage: { input_tokens: number; output_tokens: number };
  cost_usd: number | null;
  ms: number;
  model: string;
  provider: string;
  budget: Record<string, number | null>;
  tools_available: string[];
  error?: string;
  setup_hint?: string;
};

export const ask = (question: string, table: string | null, maxTurns?: number) =>
  api<Answer>("/intel/ask", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question, table, max_turns: maxTurns ?? null }),
  });

/** What a stop means, in the words the panel shows. Kept beside the type so that
 *  adding a stop state to the server and forgetting the copy is a type error. */
export const STOP_COPY: Record<Stop, string> = {
  "answered": "",
  "turn-limit": "Stopped after the turn limit — the model was still calling tools.",
  "byte-budget": "Stopped at the read budget. The answer is from what it had.",
  "spend-budget": "Stopped at the spend limit for one question.",
  "timeout": "Stopped on the clock.",
  "provider-error": "The model could not be reached.",
};
