"use client";

/** Query history and saved queries, per database.
 *
 *  Same reasoning as `useRecents`, and the same store: a query written against one
 *  database means nothing against another, so the key is the root, and switching
 *  connections switches the list with it. It is browser state because it is a
 *  convenience about how this person works — it must survive a reload, and putting
 *  it in the settings file would mean the console writes to disk every time
 *  somebody presses Run.
 *
 *  What is kept is the spec, not the result. A saved query re-runs against whatever
 *  the table is now, which is the point of saving it; a saved *result* would be a
 *  screenshot with the same name as a question.
 */

import { useCallback, useSyncExternalStore } from "react";
import type { QuerySpec } from "@/app/lib/catalog";

const HISTORY_MAX = 20;

export type StoredQuery = {
  id: string;
  table: string;
  spec: QuerySpec;
  /** Only for saved ones. History entries are identified by what they did. */
  name?: string;
  at: number;
  /** What it cost the last time it ran here, so an expensive query can be
   *  recognised before it is run again. Never treated as current: it describes a
   *  past run, against a version that may have moved. */
  last?: { read_bytes: number; ms: number; returned: number; version: number };
};

const listeners = new Set<() => void>();
const cache = new Map<string, StoredQuery[]>();
const EMPTY: StoredQuery[] = [];

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

function snapshot(k: string | null): StoredQuery[] {
  if (!k) return EMPTY;
  const hit = cache.get(k);
  if (hit) return hit;
  let value: StoredQuery[] = EMPTY;
  try {
    const raw = localStorage.getItem(k);
    const parsed = raw ? JSON.parse(raw) : null;
    if (Array.isArray(parsed)) {
      value = parsed.filter(
        (v) => v && typeof v.id === "string" && typeof v.table === "string" && v.spec,
      );
    }
  } catch {
    // Private windows, blocked site data, or something else's key at this name.
  }
  cache.set(k, value);
  return value;
}

/** Used during SSR, where there is no localStorage to read. */
function serverSnapshot(): StoredQuery[] {
  return EMPTY;
}

function commit(k: string, next: StoredQuery[]) {
  cache.set(k, next);
  try {
    localStorage.setItem(k, JSON.stringify(next));
  } catch {
    // Not being able to remember is survivable; failing to render is not.
  }
  listeners.forEach((fn) => fn());
}

/** A stable identity for a query, so re-running the same thing moves it up the
 *  history rather than filling the list with copies of itself. */
export function specKey(table: string, spec: QuerySpec): string {
  return [
    table, spec.mode, spec.filter ?? "", spec.text ?? "", spec.vector_column ?? "",
    String(spec.like_row ?? ""), String(spec.k ?? ""), String(spec.limit ?? ""),
  ].join(" ");
}

/** A one-line description, for a list where the spec itself would be unreadable. */
export function describeSpec(spec: QuerySpec): string {
  const bits: string[] = [];
  if (spec.mode === "fts" || spec.mode === "hybrid") bits.push(`"${spec.text ?? ""}"`);
  if (spec.mode === "vector" || spec.mode === "hybrid") {
    bits.push(`like row ${spec.like_row ?? 0}`);
  }
  if (spec.filter) bits.push(spec.filter);
  return bits.join(" and ") || "everything";
}

function useStore(kind: "history" | "saved", root: string | null) {
  const k = root ? `lancescope:${kind}:${root}` : null;
  const list = useSyncExternalStore(subscribe, () => snapshot(k), serverSnapshot);
  return [k, list] as const;
}

/** Every query run against this database, newest first, deduplicated by what it does. */
export function useQueryHistory(root: string | null) {
  const [k, history] = useStore("history", root);

  const record = useCallback(
    (table: string, spec: QuerySpec, last?: StoredQuery["last"]) => {
      if (!k) return;
      const id = specKey(table, spec);
      const entry: StoredQuery = { id, table, spec, at: Date.now(), last };
      commit(k, [entry, ...snapshot(k).filter((q) => q.id !== id)].slice(0, HISTORY_MAX));
    },
    [k],
  );

  const clear = useCallback(() => {
    if (k) commit(k, []);
  }, [k]);

  return { history, record, clear };
}

/** The queries someone gave a name to. Kept until they remove them. */
export function useSavedQueries(root: string | null) {
  const [k, saved] = useStore("saved", root);

  const save = useCallback(
    (name: string, table: string, spec: QuerySpec) => {
      if (!k) return;
      const id = specKey(table, spec);
      const entry: StoredQuery = { id, table, spec, name, at: Date.now() };
      commit(k, [entry, ...snapshot(k).filter((q) => q.id !== id)]);
    },
    [k],
  );

  const remove = useCallback(
    (id: string) => {
      if (!k) return;
      commit(k, snapshot(k).filter((q) => q.id !== id));
    },
    [k],
  );

  return { saved, save, remove };
}
