"use client";

/** The only part of this that is always mounted: ten keys' worth of state and a listener.
 *
 *  The game is a dynamic import that does not happen until the sequence lands, so the
 *  cost of the egg to every page that will never see it is a matcher and one keydown
 *  handler. `ssr: false` is not optional — this is a static export, so every route is
 *  prerendered at build time and a canvas game has nothing to say to a build.
 *
 *  Nothing in the interface hints at this. That is the point of one.
 */

import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";
import { createMatcher } from "./konami";

const Egg = dynamic(() => import("./Egg"), { ssr: false });

export default function EasterEgg() {
  const [open, setOpen] = useState(false);
  const openRef = useRef(false);
  // Stable, because the game subscribes to it.
  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    openRef.current = open;
  }, [open]);

  useEffect(() => {
    const m = createMatcher();

    const onKey = (e: KeyboardEvent) => {
      // Once the game is up it owns the keyboard, and the arrow keys mean something
      // else there.
      if (openRef.current) return;

      // `key` is required of a real keystroke and missing from plenty of synthetic ones —
      // a password manager filling a field, an automation harness, an extension replaying
      // input. Reading it unguarded throws. Same guard as ThemeToggle, same reason.
      if (!e.key) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      // A held key repeats. Counting the repeats fills the buffer with one key and kills
      // the run — a lean on the arrow key should be one press, the way it looks.
      if (e.repeat) return;

      // Someone typing `a` into a filter is typing, not conjuring.
      const el = document.activeElement;
      if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) return;
      if (el instanceof HTMLSelectElement) return;
      if (el instanceof HTMLElement && el.isContentEditable) return;

      if (m.push(e.key)) {
        setOpen(true);
        return;
      }

      // A stray arrow key still scrolls the page — swallowing every one of them to
      // protect a secret would be a bug that everybody hits and nobody can explain.
      // Two keys in, the odds are a player, and a page sliding out from under them is
      // worse than a page that did not scroll.
      if (m.depth() >= 2 && e.key.startsWith("Arrow")) e.preventDefault();
    };

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!open) return null;
  return <Egg onClose={close} />;
}
