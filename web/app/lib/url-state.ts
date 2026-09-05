"use client";

/** Where you are, kept in the URL rather than in a copy of it.
 *
 *  Generalised from `console/settings/page.tsx`, which has done this for one
 *  parameter since it was written and states the reason: holding the selection in
 *  `useState` and seeding it from the query string inside an effect renders the
 *  wrong panel first and then corrects itself. Subscribing to the location renders
 *  the right one, and lets the server snapshot stay honest about knowing nothing.
 *
 *  `window.location` rather than `useSearchParams`, deliberately and for the same
 *  reason the console already gives: it keeps these pages prerendering without a
 *  Suspense boundary around the whole workspace, which matters more now that a
 *  layout wraps every console route.
 *
 *  `pushState` and `replaceState` fire no event of their own, so writing one
 *  dispatches this. Both are integrated with the Next router, so a `popstate` from
 *  the back button arrives here too.
 */

import { useCallback, useSyncExternalStore } from "react";

const URL_CHANGED = "lancescope:url";

/** Push or replace, and the difference is what the back button is for.
 *
 *  A screen or a table is somewhere you were and might want to return to. A tab
 *  within one document, or a row you stepped onto with `j`, is another view of the
 *  same place — twenty of those in the history is a back button nobody can use.
 *  The settings page already draws this line in these words: "switching one is a
 *  view change rather than a navigation."
 */
export type How = "push" | "replace";

function subscribe(onChange: () => void): () => void {
  window.addEventListener("popstate", onChange);
  window.addEventListener(URL_CHANGED, onChange);
  return () => {
    window.removeEventListener("popstate", onChange);
    window.removeEventListener(URL_CHANGED, onChange);
  };
}

/** Snapshots must be referentially stable or `useSyncExternalStore` loops, and
 *  `location.search` is a fresh string on every read only when it changes — which
 *  is the property that makes it usable as one. */
function search(): string {
  return typeof window === "undefined" ? "" : window.location.search;
}

function serverSearch(): string {
  return "";
}

/** One parameter, validated against what it is allowed to be.
 *
 *  A value that is not in `allowed` falls back rather than rendering a screen that
 *  does not exist — the console has always done this for `?tab=`, and a link that
 *  has gone stale should land somewhere real rather than nowhere.
 */
export function useParam<T extends string>(
  key: string,
  allowed: readonly T[],
  fallback: T,
): T {
  const query = useSyncExternalStore(subscribe, search, serverSearch);
  const want = new URLSearchParams(query).get(key);
  return allowed.includes(want as T) ? (want as T) : fallback;
}

/** One free-text parameter — a table name, a query — with no fixed vocabulary. */
export function useValue(key: string): string | null {
  const query = useSyncExternalStore(subscribe, search, serverSearch);
  return new URLSearchParams(query).get(key);
}

/** Write some parameters. `null` removes one.
 *
 *  Coalesced against what is already there, so a click that sets a table and a
 *  screen together leaves one history entry rather than two, and a write that
 *  changes nothing leaves none at all — which is what stops a re-render loop when
 *  an effect writes what it just read.
 */
export function setParams(patch: Record<string, string | null>, how: How = "push") {
  const url = new URL(window.location.href);
  let changed = false;
  for (const [key, value] of Object.entries(patch)) {
    const now = url.searchParams.get(key);
    if (value === null) {
      if (now !== null) {
        url.searchParams.delete(key);
        changed = true;
      }
    } else if (now !== value) {
      url.searchParams.set(key, value);
      changed = true;
    }
  }
  if (!changed) return;
  window.history[how === "push" ? "pushState" : "replaceState"](null, "", url);
  window.dispatchEvent(new Event(URL_CHANGED));
}

export function useSetParams() {
  return useCallback(
    (patch: Record<string, string | null>, how: How = "push") => setParams(patch, how),
    [],
  );
}

/** Whether there is anything behind us *in this session*.
 *
 *  The browser will not say, and a back button that is always enabled is a control
 *  that sometimes does nothing. So this counts our own pushes, which is the honest
 *  scope: it knows about the moves made inside the workspace and says so, rather
 *  than guessing about the tab's history before it.
 */
let depth = 0;
let at = 0;
const depthListeners = new Set<() => void>();
let snapshot = { back: false, forward: false };

function announce() {
  const next = { back: at > 0, forward: at < depth };
  if (next.back !== snapshot.back || next.forward !== snapshot.forward) {
    snapshot = next;
  }
  depthListeners.forEach((fn) => fn());
}

if (typeof window !== "undefined") {
  window.addEventListener("popstate", () => {
    // Which direction cannot be known from the event, and guessing would be worse
    // than a control that is occasionally enabled with nothing behind it. Treating
    // every pop as a step back keeps `forward` truthful, which is the one people
    // press by accident.
    at = Math.max(0, at - 1);
    announce();
  });
  window.addEventListener(URL_CHANGED, () => {
    announce();
  });
}

export function notePush() {
  at += 1;
  depth = at;
  announce();
}

const EMPTY = { back: false, forward: false };

export function useHistoryDepth() {
  return useSyncExternalStore(
    (fn) => {
      depthListeners.add(fn);
      return () => depthListeners.delete(fn);
    },
    () => snapshot,
    () => EMPTY,
  );
}
