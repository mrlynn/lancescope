"use client";

/** Source directories this person has surveyed, most recent first.
 *
 *  Keyed globally rather than per database, unlike `recents.ts`. A folder of photos
 *  is a fact about this machine; it does not become a different folder because the
 *  console is now pointed at somebody else's database, and re-typing the path after
 *  switching connections would be a small daily annoyance with no reason behind it.
 *
 *  Same machinery as `recents.ts`: localStorage is the store and React subscribes,
 *  with a referentially stable snapshot so `useSyncExternalStore` does not loop.
 */

import { useCallback, useSyncExternalStore } from "react";

const KEY = "lancescope:sources";
const MAX = 8;

const listeners = new Set<() => void>();
let cached: string[] | null = null;
const EMPTY: string[] = [];

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

function snapshot(): string[] {
  if (cached) return cached;
  let value: string[] = EMPTY;
  try {
    const parsed = JSON.parse(localStorage.getItem(KEY) ?? "null");
    if (Array.isArray(parsed)) value = parsed.filter((v) => typeof v === "string");
  } catch {
    // Private windows, blocked site data, or something else's key at this name.
  }
  cached = value;
  return value;
}

/** Used during SSR, where there is no localStorage to read. */
function serverSnapshot(): string[] {
  return EMPTY;
}

export function useSources() {
  const sources = useSyncExternalStore(subscribe, snapshot, serverSnapshot);

  const remember = useCallback((path: string) => {
    const p = path.trim();
    if (!p) return;
    const next = [p, ...snapshot().filter((s) => s !== p)].slice(0, MAX);
    cached = next;
    try {
      localStorage.setItem(KEY, JSON.stringify(next));
    } catch {
      // Not being able to remember is survivable; failing to render is not.
    }
    listeners.forEach((fn) => fn());
  }, []);

  return { sources, remember };
}
