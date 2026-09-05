"use client";

/** One keyboard listener, and one place the bindings are written down.
 *
 *  There were eight `window.addEventListener("keydown")` handlers in this app, each
 *  with its own idea of when a keystroke counts. Exactly one of them carried this,
 *  in `ThemeToggle`:
 *
 *  > `key` is required of a real keystroke and missing from plenty of synthetic
 *  > ones — a password manager filling a field, an automation harness, an extension
 *  > replaying input. Reading it unguarded throws on those and takes the whole page
 *  > down over a shortcut nobody pressed.
 *
 *  That is the argument for a registry, and it is not tidiness: the guard was
 *  learned once and inherited by nothing. Here every binding gets it, along with the
 *  rest of the rules that were being restated or forgotten — do not fire inside a
 *  text field unless the binding says so, do not fire under an open `<dialog>`, and
 *  never `preventDefault` a chord we do not own.
 *
 *  Handlers are registered *by* the components that own them rather than reached
 *  into from here. `run()` in the query workspace is 400 lines deep in state that
 *  belongs to it; lifting it so a central switch could call it would be the wrong
 *  way round.
 */

import { useEffect } from "react";

/** What a binding does, named rather than described, so the `?` sheet and the
 *  dispatcher agree by construction. */
export type Action =
  | "palette"
  | "primary"
  | "copy-diagnostic"
  | "focus-tables"
  | "screen-1" | "screen-2" | "screen-3" | "screen-4" | "screen-5"
  | "theme"
  | "shortcuts";

export type Binding = {
  action: Action;
  /** As typed, for the sheet. */
  keys: string;
  says: string;
  /** ⌘ on a Mac, Ctrl elsewhere — the kiosk runs on Linux and Windows too. */
  mod?: boolean;
  shift?: boolean;
  key: string;
  /** Fires even with a text field focused. ⌘K and ⌘↵ are useless otherwise: the
   *  first is reached for *while* typing a filter, and the second runs it. */
  inInput?: boolean;
};

export const BINDINGS: Binding[] = [
  { action: "palette", keys: "⌘K", says: "Go to anything", mod: true, key: "k", inInput: true },
  { action: "primary", keys: "⌘↵", says: "Run what this screen is for", mod: true, key: "enter", inInput: true },
  { action: "copy-diagnostic", keys: "⌘⇧C", says: "Copy a reproducible diagnostic", mod: true, shift: true, key: "c", inInput: true },
  { action: "focus-tables", keys: "/", says: "Filter tables", key: "/" },
  { action: "screen-1", keys: "⌘1", says: "Table", mod: true, key: "1" },
  { action: "screen-2", keys: "⌘2", says: "Query", mod: true, key: "2" },
  { action: "screen-3", keys: "⌘3", says: "Compare", mod: true, key: "3" },
  { action: "screen-4", keys: "⌘4", says: "Training", mod: true, key: "4" },
  { action: "screen-5", keys: "⌘5", says: "Data", mod: true, key: "5" },
  { action: "theme", keys: "T", says: "Cycle the theme", key: "t" },
  { action: "shortcuts", keys: "?", says: "This list", shift: true, key: "?" },
];

/** The last registration for an action wins.
 *
 *  A screen registers `primary` on mount and drops it on unmount, so ⌘↵ means
 *  whatever the screen in front of you is for. A stack rather than a single slot,
 *  because two things can be mounted at once and the inner one should win — the
 *  query workspace stays mounted under an open row panel.
 */
const handlers = new Map<Action, (() => void)[]>();

export function register(action: Action, fn: () => void): () => void {
  const stack = handlers.get(action) ?? [];
  stack.push(fn);
  handlers.set(action, stack);
  return () => {
    const now = handlers.get(action);
    if (!now) return;
    const at = now.indexOf(fn);
    if (at >= 0) now.splice(at, 1);
  };
}

/** Whether anything is listening, so a control can be disabled rather than dead. */
export function isBound(action: Action): boolean {
  return (handlers.get(action) ?? []).length > 0;
}

function fire(action: Action): boolean {
  const stack = handlers.get(action) ?? [];
  const fn = stack[stack.length - 1];
  if (!fn) return false;
  fn();
  return true;
}

/** Register a handler for as long as this component is mounted. */
export function useShortcut(action: Action, fn: (() => void) | null) {
  useEffect(() => {
    if (!fn) return;
    return register(action, fn);
  }, [action, fn]);
}

function typing(): boolean {
  const el = document.activeElement;
  return (
    el instanceof HTMLInputElement ||
    el instanceof HTMLTextAreaElement ||
    el instanceof HTMLSelectElement ||
    (el instanceof HTMLElement && el.isContentEditable)
  );
}

/** Installed once, by the shell. */
export function listen(): () => void {
  const onKey = (e: KeyboardEvent) => {
    // The guard that was learned once and inherited by nothing.
    if (!e.key) return;

    // A dialog is modal by definition; the app underneath it is not being driven.
    // Escape belongs to the dialog and is not ours to take.
    if (document.querySelector("dialog[open]")) return;

    const mod = e.metaKey || e.ctrlKey;
    const key = e.key.toLowerCase();

    for (const b of BINDINGS) {
      if (!!b.mod !== mod) continue;
      if (!!b.shift !== e.shiftKey) continue;
      if (e.altKey) continue;
      if (key !== b.key) continue;
      if (!b.inInput && typing()) continue;
      // Claimed only once something is actually listening. An unbound chord stays
      // the browser's — ⌘1 switches tabs in a browser and should keep doing so on a
      // screen that has no fifth section to select.
      if (!fire(b.action)) continue;
      e.preventDefault();
      return;
    }
  };
  window.addEventListener("keydown", onKey);
  return () => window.removeEventListener("keydown", onKey);
}
