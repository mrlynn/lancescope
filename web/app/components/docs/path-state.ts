"use client";

/** Which path the reader is on, remembered.
 *
 *  The same shape as the theme: the value lives outside React, a custom event says
 *  when it changed, and `useSyncExternalStore` reads it. That is not consistency for
 *  its own sake — it is what makes the value survive a page navigation in a
 *  statically exported guide, where every page is its own document and React state
 *  does not cross the gap.
 *
 *  `null` on the server and through hydration, so a page never renders one reader's
 *  path and then swaps to another's.
 */

import { useSyncExternalStore } from "react";
import { PATHS, type Path, pathById } from "@/app/lib/paths";

export const PATH_KEY = "lancescope-path";
const EVENT = "lancescope:pathchange";

function read(): string | null {
  try {
    const id = localStorage.getItem(PATH_KEY);
    // A path that no longer exists is the same as no path, and quietly: a reader
    // who chose one we have since renamed should get the picker, not an error.
    return id && PATHS.some((p) => p.id === id) ? id : null;
  } catch {
    // Private windows and blocked site data. The guide reads fine without this.
    return null;
  }
}

const serverSnapshot = () => null;

function subscribe(onChange: () => void) {
  window.addEventListener(EVENT, onChange);
  // Another tab choosing a path is a change here too.
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

export function choosePath(id: string | null) {
  try {
    if (id) localStorage.setItem(PATH_KEY, id);
    else localStorage.removeItem(PATH_KEY);
  } catch {
    // Not remembered, but still applied for this page.
  }
  window.dispatchEvent(new Event(EVENT));
}

/** The chosen path, or null — including through hydration. */
export function useChosenPath(): Path | null {
  const id = useSyncExternalStore(subscribe, read, serverSnapshot);
  return pathById(id) ?? null;
}
