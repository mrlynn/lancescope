"use client";

import { useEffect, useSyncExternalStore } from "react";

export type Theme = "light" | "dark";

export const THEME_KEY = "lancescope-theme";
const EVENT = "lancescope:themechange";

/** The document *is* the store. The inline script in layout.tsx resolves the theme
 *  before first paint, so `<html data-theme>` is authoritative by the time any of
 *  this runs — reading it beats keeping a second copy in React state that can
 *  disagree with what the user is looking at. */
function getSnapshot(): Theme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

/** Used during SSR and hydration, where there is no document to read. React
 *  re-renders with the real snapshot immediately afterwards. */
function getServerSnapshot(): Theme {
  return "dark";
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener(EVENT, onChange);
  return () => window.removeEventListener(EVENT, onChange);
}

export function applyTheme(t: Theme) {
  document.documentElement.dataset.theme = t;
  try {
    localStorage.setItem(THEME_KEY, t);
  } catch {
    // Private windows and blocked site data. The theme still applies to this
    // page; it just will not be remembered.
  }
  window.dispatchEvent(new Event(EVENT));
}

export default function ThemeToggle() {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  const other: Theme = theme === "dark" ? "light" : "dark";

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() !== "t" || e.metaKey || e.ctrlKey || e.altKey) return;
      const el = document.activeElement;
      if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) return;
      applyTheme(getSnapshot() === "dark" ? "light" : "dark");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <button
      onClick={() => applyTheme(other)}
      title={`Switch to ${other} — T`}
      aria-label={`Switch to ${other} theme`}
      className="mono text-[10px] tracking-[0.14em] uppercase px-2.5 py-1.5 rounded-sm
                 border border-[var(--rule)] text-[var(--haze)]
                 hover:text-[var(--bright)] hover:border-[var(--haze)] transition-colors"
    >
      {other}
    </button>
  );
}
