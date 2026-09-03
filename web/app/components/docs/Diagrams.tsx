"use client";

/** Mermaid blocks in the guide, drawn.
 *
 *  Three things decide the shape of this.
 *
 *  It is the only part of the guide that runs in a browser. Everything else —
 *  markdown, front matter, syntax highlighting — happens once at build time and
 *  arrives as HTML. Mermaid cannot: laying out a graph needs real text measurement,
 *  which needs a real DOM, which at build time would mean shipping a headless
 *  browser as a build dependency. So it renders on the client, and only on the pages
 *  that have a diagram: `doc.diagrams` gates the import, and the import is dynamic,
 *  so a reader on a page with no diagram never fetches the library at all.
 *
 *  Its palette is read rather than declared. `getComputedStyle` on the document
 *  gives back whatever the theme currently resolves to, so a diagram is in key with
 *  the page around it in light and dark, and stays in key if the palette changes —
 *  without a second copy of the colours living here to drift out of step with
 *  globals.css.
 *
 *  A diagram that will not draw leaves its source on screen. The markdown ships the
 *  source inside the figure, so the failure case is the page as it looked before any
 *  of this existed, plus a line saying why. A blank space where a picture should be
 *  is the one outcome worth engineering against.
 */

import { useEffect, useSyncExternalStore } from "react";

/** The event `applyTheme` fires. Imported rather than re-declared would be better,
 *  but ThemeToggle exports a component and this needs only the string. */
const THEME_EVENT = "lancescope:themechange";

function palette() {
  const s = getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) =>
    s.getPropertyValue(name).trim() || fallback;
  return {
    ink: v("--ink", "#171513"),
    ink2: v("--ink-2", "#1e1b19"),
    ink3: v("--ink-3", "#241f1c"),
    rule: v("--rule", "#5d534d"),
    haze: v("--haze", "#a3958c"),
    body: v("--body", "#c3b5ab"),
    bright: v("--bright", "#f4ebe8"),
    index: v("--index", "#d9a05b"),
    video: v("--video", "#ff734a"),
    sans: v("--font-sans", "ui-sans-serif"),
  };
}

/** Mermaid's `base` theme with our variables, rather than one of its bundled
 *  themes. The bundled ones are a different design language — rounded lavender
 *  boxes on white — and a diagram that looks like it came from another product is
 *  worse than no diagram. */
function themeVariables() {
  const p = palette();
  return {
    darkMode: false,                  // we supply every colour; this only picks defaults
    background: "transparent",
    fontFamily: `${p.sans}, ui-sans-serif, system-ui, sans-serif`,
    fontSize: "13px",

    // Node outlines are --haze rather than --rule, and the reason is measured. A
    // panel in the console is a --rule border around --ink-2 and reads fine, because
    // it is one big shape with whitespace around it. A graph is forty small ones: at
    // --rule the border sits at 2.4:1 on the canvas and the fill at 1.1:1, so a node
    // stops looking like a node. --haze is 6.3:1 and the box comes back.
    primaryColor: p.ink3,
    primaryTextColor: p.bright,
    primaryBorderColor: p.haze,
    secondaryColor: p.ink2,
    secondaryTextColor: p.bright,
    secondaryBorderColor: p.haze,
    tertiaryColor: p.ink2,
    tertiaryTextColor: p.bright,
    tertiaryBorderColor: p.haze,

    lineColor: p.haze,
    // Labels are the content of a diagram, so they get the tier the console gives
    // every other number and name it wants read: --bright, 15.5:1 on the canvas.
    textColor: p.bright,
    mainBkg: p.ink3,
    nodeBorder: p.haze,
    nodeTextColor: p.bright,
    // Subgraphs group rather than say, so their outline stays the quiet tier — the
    // one place --rule is right here.
    clusterBkg: "transparent",
    clusterBorder: p.rule,
    titleColor: p.haze,
    edgeLabelBackground: p.ink,

    // The two accents carry the same meaning they carry everywhere else in the
    // console: amber for what a read costs, coral for the heavy half.
    labelBoxBorderColor: p.index,
    labelTextColor: p.body,
    actorBorder: p.haze,
    actorBkg: p.ink3,
    actorTextColor: p.bright,
    signalColor: p.haze,
    signalTextColor: p.bright,
    noteBkgColor: p.ink2,
    noteTextColor: p.body,
    noteBorderColor: p.index,
  };
}

/** The theme as it actually resolves: an explicit choice if there is one, the OS
 *  otherwise. Same three states the toggle has, collapsed to the two a palette can
 *  be in. */
function resolvedTheme(): "light" | "dark" {
  const chosen = document.documentElement.dataset.theme;
  if (chosen === "light" || chosen === "dark") return chosen;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Null on the server and through hydration, where there is no document to read.
 *  The effect below draws nothing until it is a real theme, so a diagram is never
 *  drawn once in the wrong palette and then again in the right one. */
const serverTheme = () => null;

function subscribeToTheme(onChange: () => void) {
  const media = window.matchMedia?.("(prefers-color-scheme: dark)");
  window.addEventListener(THEME_EVENT, onChange);
  media?.addEventListener("change", onChange);
  return () => {
    window.removeEventListener(THEME_EVENT, onChange);
    media?.removeEventListener("change", onChange);
  };
}

export default function Diagrams() {
  // Redrawn on a theme change rather than recoloured: mermaid bakes its palette into
  // the SVG it produces, so drawing it again is the only way to change it.
  const theme = useSyncExternalStore(subscribeToTheme, resolvedTheme, serverTheme);

  useEffect(() => {
    if (theme === null) return;
    let live = true;

    (async () => {
      const figures = Array.from(
        document.querySelectorAll<HTMLElement>("figure.mermaid[data-mermaid]"),
      );
      if (figures.length === 0) return;

      // The source is read back off the figure on every pass, so a re-render after a
      // theme change is drawn from the markdown rather than from the SVG it produced
      // last time.
      for (const f of figures) {
        if (f.dataset.source === undefined) {
          f.dataset.source = f.querySelector("pre")?.textContent ?? "";
        }
      }

      let mermaid;
      try {
        mermaid = (await import("mermaid")).default;
      } catch {
        for (const f of figures) fail(f, "the diagram renderer could not be loaded");
        return;
      }
      if (!live) return;

      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: "base",
        themeVariables: themeVariables(),
      });

      for (const [i, f] of figures.entries()) {
        const source = f.dataset.source ?? "";
        try {
          const { svg } = await mermaid.render(`d${i}-${theme}-${Date.now()}`, source);
          if (!live) return;
          f.innerHTML = svg;
          f.dataset.state = "drawn";
        } catch (e) {
          if (!live) return;
          fail(f, e instanceof Error ? e.message : "this diagram could not be drawn");
        }
      }
    })();

    return () => { live = false; };
  }, [theme]);

  return null;
}

/** Put the source back and say what went wrong, rather than leaving a hole. */
function fail(f: HTMLElement, why: string) {
  const source = f.dataset.source ?? "";
  const pre = document.createElement("pre");
  pre.textContent = source;
  const note = document.createElement("figcaption");
  note.textContent = why;
  f.replaceChildren(note, pre);
  f.dataset.state = "failed";
}
