// Client for the ingest API. Shapes mirror server/routes/ingest.py.
//
// Everything here is read-only today: this build can survey a directory and report
// what it would do with it, and `capabilities.writes` says so rather than the screen
// pretending otherwise.

import { ApiError, type Capability } from "@/app/lib/catalog";

export type Requirement = {
  name: string;
  kind: "binary" | "python";
  why: string;
  install_hint: string;
};

/** A capability plus what is missing, so a UI can print the remedy and not just
 *  the refusal. */
export type Readiness = Capability & { missing: Requirement[] };

export type MediaKind = "image" | "video" | "audio" | "pdf";

export type IngestCapabilities = {
  writes: Capability;
  /** Per medium: this build may be able to create a table and unable to decode a
   *  JPEG, which is one grey button and two very different remedies. */
  media: Record<MediaKind, Readiness>;
  /** Kinds the writer can turn into rows — a subset of the kinds discovery knows. */
  implemented: MediaKind[];
  embedder: ResolvedEmbedder;
  destination_default: string;
  note: string;
};

export type FoundKind = {
  kind: MediaKind;
  files: number;
  bytes: number;
  examples: string[];
  extensions: string[];
};

export type UnsupportedGroup = {
  extension: string;
  files: number;
  bytes: number;
  examples: string[];
};

export type ScanResult = {
  source: string;
  /** Three states. `null` is a remote URI: unknowable rather than empty, the same
   *  distinction the connection probe makes. */
  readable: boolean | null;
  found: FoundKind[];
  /** Kinds present but not asked about. Not `unsupported` — this tool handles them. */
  excluded: FoundKind[];
  unsupported: UnsupportedGroup[];
  readiness: Partial<Record<MediaKind, Readiness>>;
  hidden_skipped: number;
  total_files: number;
  total_bytes: number;
  ingestable_files: number;
  truncated: boolean;
  note: string;
  warnings: string[];
  ms: number;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/ingest${path}`, { cache: "no-store", ...init });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      // A non-JSON body from a proxy or a crash. The status is still the truth.
    }
    throw new ApiError(res.status, String(detail));
  }
  return res.json();
}

export const getIngestCapabilities = (destination?: string) =>
  request<IngestCapabilities>(
    `/capabilities${destination ? `?destination=${encodeURIComponent(destination)}` : ""}`,
  );

export const scanSource = (
  source: string,
  opts: { kinds?: MediaKind[]; maxFiles?: number } = {},
  signal?: AbortSignal,
) =>
  request<ScanResult>("/scan", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      source,
      kinds: opts.kinds ?? null,
      ...(opts.maxFiles ? { max_files: opts.maxFiles } : {}),
    }),
    signal,
  });

// ------------------------------------------------------------------------- jobs

export type ResolvedEmbedder = {
  backend: string;
  reason: string;
  available: boolean;
  model: string | null;
  host: string | null;
  key_source: string | null;
  setup_hint: string;
  modalities: string[];
  sees_images: boolean;
};

export type JobState =
  | "queued" | "running" | "cancelling"
  | "cancelled" | "failed" | "done" | "interrupted";

export const LIVE_STATES: JobState[] = ["queued", "running", "cancelling"];

export type JobProgress = {
  stage: string;
  files_total: number;
  files_done: number;
  files_failed: number;
  files_skipped: number;
  rows_written: number;
  source_bytes_read: number;
  current_file: string | null;
  /** Its own clock, so a single huge file does not look like a hang. */
  current_file_elapsed_s: number | null;
  /** Null until ten files are done: an estimate from three is a guess in a hat. */
  eta_s: number | null;
  elapsed_s: number;
};

export type JobFailure = { path: string; reason: string; stage: string };

export type JobResult = {
  table: string;
  uri: string;
  rows: number;
  version: number;
  vector_dim: number | null;
  embedder: { backend: string; model: string; dim: number } | null;
  indices: { column: string; kind: string; built: boolean; reason: string }[];
  failures: JobFailure[];
  failures_total: number;
  partial: boolean;
  cancelled: boolean;
  created: boolean;
  detail: string;
  warnings: string[];
  ms: number;
};

export type Job = {
  id: string;
  request: { source: string; destination: string; name: string; kinds: string[] };
  state: JobState;
  progress: JobProgress;
  result: JobResult | null;
  error: string | null;
  /** One honest sentence, rendered verbatim. This is where the tone lives. */
  detail: string;
  started: string;
  updated: string;
  finished: string | null;
  cursor: number;
};

export type StartJob = {
  source: string;
  destination: string;
  name: string;
  kinds?: MediaKind[];
  limit?: number | null;
};

export const startJob = (body: StartJob) =>
  request<Job>("/jobs", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });

export const getJob = (id: string) => request<Job>(`/jobs/${id}`);
export const cancelJob = (id: string) => request<Job>(`/jobs/${id}/cancel`, { method: "POST" });
export const adoptJob = (id: string) =>
  request<{ adopted: boolean; note: string }>(`/jobs/${id}/adopt`, { method: "POST" });
export const discardJob = (id: string) =>
  request<{ removed: boolean; detail: string }>(`/jobs/${id}/discard`, { method: "POST" });
