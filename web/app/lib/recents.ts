"use client";

/** Recents and pins for the table rail.
 *
 *  Both are per-database — a table called `moments` in the demo corpus has nothing
 *  to do with one of the same name in somebody's production bucket, and a shared
 *  list would put the wrong history in front of you every time you switched. The
 *  key is the root, so switching connections switches both lists with it.
 *
 *  This is browser state on purpose. It is a convenience about how *this* person
 *  browses, it must survive a reload but not need to survive a reinstall, and
 *  putting it in the settings file would mean the console writes to disk every
 *  time you click a table.
 *
 *  `localStorage` is the store and React subscribes to it, rather than React
 *  holding a copy that an effect refills on every root change. Two components can
 *  then read the same list — the rail and the home screen do — without either of
 *  them owning it.
 */

import { useCallback, useSyncExternalStore } from "react";

const RECENTS_MAX = 6;

const listeners = new Set<() => void>();

/** Snapshots must be referentially stable between renders or `useSyncExternalStore`
 *  will loop, so parsed lists are cached and only replaced when they change. */
const cache = new Map<string, string[]>();
const EMPTY: string[] = [];

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

function snapshot(k: string | null): string[] {
  if (!k) return EMPTY;
  const hit = cache.get(k);
  if (hit) return hit;
  let value: string[] = EMPTY;
  try {
    const raw = localStorage.getItem(k);
    const parsed = raw ? JSON.parse(raw) : null;
    if (Array.isArray(parsed)) value = parsed.filter((v) => typeof v === "string");
  } catch {
    // Private windows, blocked site data, or something else's key at this name.
  }
  cache.set(k, value);
  return value;
}

/** Used during SSR, where there is no localStorage to read. */
function serverSnapshot(): string[] {
  return EMPTY;
}

function commit(k: string, next: string[]) {
  cache.set(k, next);
  try {
    localStorage.setItem(k, JSON.stringify(next));
  } catch {
    // Not being able to remember is survivable; failing to render is not.
  }
  listeners.forEach((fn) => fn());
}

function useList(kind: "recent" | "pin", root: string | null) {
  const k = root ? `lancescope:${kind}:${root}` : null;
  const list = useSyncExternalStore(subscribe, () => snapshot(k), serverSnapshot);
  return [k, list] as const;
}

/** The tables this person opened here, most recent first. */
export function useRecents(root: string | null) {
  const [k, recents] = useList("recent", root);

  const touch = useCallback((name: string) => {
    if (!k) return;
    commit(k, [name, ...snapshot(k).filter((n) => n !== name)].slice(0, RECENTS_MAX));
  }, [k]);

  return { recents, touch };
}

/** The tables this person said they care about, in the order they were pinned. */
export function usePins(root: string | null) {
  const [k, pins] = useList("pin", root);

  const toggle = useCallback((name: string) => {
    if (!k) return;
    const now = snapshot(k);
    commit(k, now.includes(name) ? now.filter((n) => n !== name) : [...now, name]);
  }, [k]);

  return { pins, toggle };
}
