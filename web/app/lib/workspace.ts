"use client";

/** What the console knows, held where a shell can keep it.
 *
 *  Everything here used to be `useState` inside `console/page.tsx`. That was fine
 *  while the console was one page; it stops being fine once a layout wraps four
 *  routes, because state in a page is thrown away when you navigate to another one
 *  — which is how switching to settings and back used to empty the table list and
 *  refill it.
 *
 *  A module store that React subscribes to, rather than a context. Three reasons,
 *  and the first is the one that decided it:
 *
 *  - a keyboard handler outside the tree has to be able to read this, and a context
 *    cannot be read from one without a ref dance;
 *  - appending to the cost ledger would re-render every consumer of a context, and
 *    here it re-renders the pane that shows it;
 *  - it is what this codebase already does three times over. `recents.ts`,
 *    `queries.ts` and `sources.ts` are all `useSyncExternalStore` over a module,
 *    and its argument applies one layer up: the *server* is the store, and React
 *    subscribes to a cache of it rather than holding a copy an effect refills.
 *
 *  What is deliberately *not* here: which table is selected and which screen is
 *  open. Those live in the URL (`url-state.ts`), because they are where you are
 *  rather than what was fetched, and every one of them should survive being
 *  pasted to somebody else.
 */

import { useSyncExternalStore } from "react";

import {
  type Findings, type Fragments, type Indices, type TableDetail,
  type TableList, type Versions,
} from "@/app/lib/catalog";
import type { Capabilities, SettingsState } from "@/app/lib/settings";

/** One read, and what it cost. The console's whole argument is that reads have
 *  prices, so the prices are kept rather than overwritten — the header used to show
 *  the last one and throw the rest away. */
export type Cost = { label: string; bytes: number; iops: number; at: number };

export type Workspace = {
  list: TableList | null;
  listError: string | null;
  settings: SettingsState | null;
  ai: Capabilities | null;
  demoReady: boolean;

  detail: TableDetail | null;
  versions: Versions | null;
  indices: Indices | null;
  fragments: Fragments | null;
  findings: Findings | null;

  ledger: Cost[];
};

const EMPTY: Workspace = {
  list: null, listError: null, settings: null, ai: null, demoReady: false,
  detail: null, versions: null, indices: null, fragments: null, findings: null,
  ledger: [],
};

/** Kept because `useSyncExternalStore` compares snapshots by identity: return a new
 *  object from an unchanged store and it re-renders forever. Every write below
 *  replaces this once; every read hands back the same reference until it does. */
let state: Workspace = EMPTY;
const listeners = new Set<() => void>();

function snapshot(): Workspace {
  return state;
}

/** SSR, and the static export's first paint. A constant, for the same reason
 *  `recents.ts` returns one: a fresh object here is a hydration mismatch. */
function serverSnapshot(): Workspace {
  return EMPTY;
}

function commit(patch: Partial<Workspace>) {
  state = { ...state, ...patch };
  listeners.forEach((fn) => fn());
}

/** Hoisted, and it matters. An inline `subscribe` is a new function on every
 *  render, which makes React tear down and re-establish the subscription each time
 *  — and a commit that lands in that window is a notification nobody is listening
 *  for. `recents.ts` gets this right and this did not: the symptom was a panel that
 *  fetched its data, resolved it, wrote it to the store and went on showing
 *  "reading indices…". */
function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function useWorkspace(): Workspace {
  return useSyncExternalStore(subscribe, snapshot, serverSnapshot);
}

/** Read the store without subscribing — for a keyboard handler or a menu action,
 *  which wants the current value and does not want to re-render for it. */
export function read(): Workspace {
  return state;
}

export const set = commit;

/** Everything about one table, cleared.
 *
 *  Called from the click that changes the selection rather than from an effect
 *  watching it. The console has always done it this way and says why: an effect
 *  would render twice on every click, and the reset is a consequence of the click,
 *  not of the state having changed.
 */
export function clearTable() {
  commit({
    detail: null, versions: null, indices: null, fragments: null, findings: null,
  });
}

/** How many reads to keep.
 *
 *  Enough to see a session's shape, few enough that the pane is readable without
 *  scrolling to the bottom for the one that just happened. */
const LEDGER_MAX = 40;

/** What a request cost, recorded rather than displayed and discarded.
 *
 *  Exported bare because two of the callers are not the shell: a query's cost
 *  arrives inside `QueryTab` and a column weight inside `TrainingTab`, and routing
 *  those through a store action would mean the store knowing about panels.
 */
export function recordCost(label: string, bytes: number, iops = 0) {
  const entry: Cost = { label, bytes, iops, at: Date.now() };
  commit({ ledger: [entry, ...state.ledger].slice(0, LEDGER_MAX) });
}

/** The scope the cost pane states out loud: this console, since it opened.
 *
 *  Not persisted, deliberately. A total that survived a reload would silently stop
 *  meaning "what I have read" and start meaning something nobody asked for — the
 *  same scope `TokenSpend` already names for tokens.
 */
export function totals() {
  return state.ledger.reduce(
    (sum, e) => ({ bytes: sum.bytes + e.bytes, iops: sum.iops + e.iops }),
    { bytes: 0, iops: 0 },
  );
}

/** Emptied on a connection switch, along with everything else that was about the
 *  database we just left. */
export function clearConnection() {
  commit({
    list: null, listError: null, ledger: [],
    detail: null, versions: null, indices: null, fragments: null, findings: null,
  });
}
