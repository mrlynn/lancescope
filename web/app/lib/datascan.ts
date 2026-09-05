"use client";

/** Client for the checks that read the data. Shapes mirror server/routes/datascan.py.
 *
 *  A separate module from `catalog.ts` for the reason the router is separate from
 *  `/catalog`: everything there reads manifests and costs kilobytes, everything here
 *  reads columns. Two clients keeps the boundary visible at the import.
 */

import { ApiError, type Capability, type Finding } from "@/app/lib/catalog";

export type CheckPlan = {
  check: string;
  title: string;
  what: string;
  capability: Capability;
  columns: string[];
  /** Null where the check cannot honestly be weighed rather than where it is free —
   *  an index probe is not a projection the footers can describe. */
  estimate: {
    bytes: number; floor_bytes: number; blob_bytes: number | null;
    caveats: string[]; footer_bytes: number; footer_ms: number; off_meter: true;
  } | null;
  estimate_reason: string;
  quote: string;
};

export type ScanPlan = {
  name: string;
  survey: {
    rows: number;
    columns: { name: string; type: string; blob: boolean; vector_dim: number | null;
               scalar: boolean; indexed: boolean }[];
  };
  checks: CheckPlan[];
  total_bytes: number;
  quoted_from: string;
  off_meter: true;
  read_bytes: number;
  read_iops: number;
};

export type CheckResult = {
  check: string;
  findings: Finding[];
  columns: string[];
  read_bytes: number;
  read_iops: number;
  ms: number;
  state: "done" | "cancelled" | "failed" | "unsupported";
  error: string;
  detail: string;
};

export type ScanJob = {
  id: string;
  table: string;
  /** The version this answer is about. A distribution reported against "the table"
   *  is a claim with no moment attached, and a scan takes long enough to matter. */
  version: number;
  state: "queued" | "running" | "cancelling" | "cancelled" | "failed" | "done";
  selections: { check: string; columns: string[] }[];
  progress: { checks_total: number; checks_done: number; current: string };
  results: CheckResult[];
  findings: Finding[];
  read_bytes: number;
  read_iops: number;
  error: string;
  detail: string;
  started: string;
  updated: string;
  finished: string | null;
};

export const LIVE_STATES = ["queued", "running", "cancelling"];

export type Selection = { check: string; columns: string[] };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/scan${path}`, { cache: "no-store", ...init });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      // A 409 carries the running job's id inside `detail`, because the caller who
      // pressed the button twice wants the first job rather than an apology.
      detail = typeof body.detail === "object"
        ? (body.detail.detail ?? detail) : (body.detail ?? detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, String(detail));
  }
  return res.json();
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

/** What each check would read, before any of it is read. */
export const planScan = (table: string, selections: Selection[] = []) =>
  post<ScanPlan>(`/tables/${table}/plan`, { selections });

export const startScan = (table: string, selections: Selection[]) =>
  post<ScanJob>(`/tables/${table}`, { selections });

export const getScanJob = (id: string) => request<ScanJob>(`/jobs/${id}`);
export const cancelScanJob = (id: string) => post<ScanJob>(`/jobs/${id}/cancel`);
export const listScanJobs = () => request<{ jobs: ScanJob[] }>("/jobs");
