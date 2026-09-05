"use client";

import { useCallback, useSyncExternalStore } from "react";

import { useShortcut } from "@/app/lib/keys";
import Icon, { type IconName } from "@/app/components/Icon";

/** Three states, not two. An absent `data-theme` attribute is the third one, and
 *  the old two-way button gave no way back to it once you had chosen, and no way
 *  to see which of the three you were in. A segmented control shows all three at
 *  once and costs the same width the word LIGHT used to.
 *
 *  The first state used to mean "follow the OS". It resolves to light while dark
 *  is under repair — see the palette comment in globals.css — so it is labelled
 *  for what it does rather than for what it was called. Dark is still one click
 *  away; it is just no longer inherited from a system setting. */
export type Choice = "system" | "light" | "dark";
export type Theme = "light" | "dark";

export const THEME_KEY = "lancescope-theme";
const EVENT = "lancescope:themechange";

const OPTIONS: { id: Choice; icon: IconName; label: string }[] = [
  { id: "system", icon: "system", label: "No preference — light for now" },
  { id: "light", icon: "sun", label: "Light" },
  { id: "dark", icon: "moon", label: "Dark" },
];

/** The document *is* the store. The inline script in layout.tsx resolves the theme
 *  before first paint, so `<html data-theme>` is authoritative by the time any of
 *  this runs — reading it beats keeping a second copy in React state that can
 *  disagree with what the user is looking at. */
function getSnapshot(): Choice {
  const t = document.documentElement.dataset.theme;
  return t === "light" || t === "dark" ? t : "system";
}

/** Used during SSR and hydration, where there is no document to read. React
 *  re-renders with the real snapshot immediately afterwards. */
function getServerSnapshot(): Choice {
  return "system";
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener(EVENT, onChange);
  return () => window.removeEventListener(EVENT, onChange);
}

export function applyTheme(c: Choice) {
  if (c === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = c;
  try {
    if (c === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, c);
  } catch {
    // Private windows and blocked site data. The theme still applies to this
    // page; it just will not be remembered.
  }
  window.dispatchEvent(new Event(EVENT));
}

export default function ThemeToggle() {
  const choice = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  // `T`, through the one listener. The guard this used to carry alone — a synthetic
  // keystroke with no `key` throwing and taking the page down — now lives in the
  // registry, where every binding inherits it.
  useShortcut("theme", useCallback(() => {
    const i = OPTIONS.findIndex((o) => o.id === getSnapshot());
    applyTheme(OPTIONS[(i + 1) % OPTIONS.length].id);
  }, []));

  return (
    <div className="seg" role="group" aria-label="Colour theme">
      {OPTIONS.map((o) => (
        <button
          key={o.id}
          onClick={() => applyTheme(o.id)}
          aria-pressed={choice === o.id}
          aria-label={o.label}
          data-tip={`${o.label} — T cycles`}
        >
          <Icon name={o.icon} size={15} />
        </button>
      ))}
    </div>
  );
}
