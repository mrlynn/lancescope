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
  media: Record<MediaKind, Readiness>;
  embedders: string[];
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
