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

/** The theme as it actually resolves. An explicit choice if there is one, and
 *  light otherwise — the same rule the CSS follows now that dark is applied only
 *  on `[data-theme="dark"]`. Reading `prefers-color-scheme` here instead would
 *  draw a dark diagram onto a light page for anyone whose OS is set to dark and
 *  who has never touched the toggle. */
function resolvedTheme(): "light" | "dark" {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

/** Null on the server and through hydration, where there is no document to read.
 *  The effect below draws nothing until it is a real theme, so a diagram is never
 *  drawn once in the wrong palette and then again in the right one. */
const serverTheme = () => null;

function subscribeToTheme(onChange: () => void) {
  // Only the toggle can change the palette now, so its event is the only source
  // worth listening to. An OS change moves nothing and should redraw nothing.
  window.addEventListener(THEME_EVENT, onChange);
  return () => window.removeEventListener(THEME_EVENT, onChange);
}

export default function Diagrams() {
  // Redrawn on a theme change rather than recoloured: mermaid bakes its palette into
  // the SVG it produces, so drawing it again is the only way to change it.
  const theme = useSyncExternalStore(subscribeToTheme, resolvedTheme, serverTheme);

  useEffect(() => {
    if (theme === null) return;
    let live = true;
    const cleanups: (() => void)[] = [];

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
        // Tighter than mermaid's defaults, because its defaults are drawn for a page
        // with no other column on it. A graph of five subgraphs lays out at 2,000px
        // at 50/50 spacing, and the guide has 704px of column to put it in; pulling
        // the spacing in and letting long labels wrap costs nothing a reader values
        // and buys back most of that width.
        flowchart: {
          nodeSpacing: 26,
          rankSpacing: 38,
          padding: 10,
          useMaxWidth: false,
          htmlLabels: true,
          wrappingWidth: 150,
        },
      });

      for (const [i, f] of figures.entries()) {
        const source = f.dataset.source ?? "";
        try {
          const { svg } = await mermaid.render(`d${i}-${theme}-${Date.now()}`, source);
          if (!live) return;
          f.innerHTML = svg;
          f.dataset.state = "drawn";
          size(f);
          addExpander(f);
        } catch (e) {
          if (!live) return;
          fail(f, e instanceof Error ? e.message : "this diagram could not be drawn");
        }
      }

      // A diagram laid out for 2,000px and shown in a 704px column is re-sized by
      // the reader's window, so it has to be re-sized again when that changes.
      const onResize = () => {
        for (const f of document.querySelectorAll<HTMLElement>(
          'figure.mermaid[data-state="drawn"]')) size(f);
      };
      window.addEventListener("resize", onResize);
      cleanups.push(() => window.removeEventListener("resize", onResize));
    })();

    return () => {
      live = false;
      for (const c of cleanups) c();
    };
  }, [theme]);

  return null;
}

/** Below this, a diagram is a picture of a diagram.
 *
 *  Mermaid lays a graph out at whatever width it needs — 2,022px for one on the
 *  architecture page — and the guide's prose column is 704px. Fitting the first into
 *  the second is a scale of 0.35, which takes 13px labels down to 4.5px. Measured,
 *  not guessed: that is what this page was doing, and it is why the diagrams could
 *  not be read.
 *
 *  So width-fitting stops at the point the text stops being text. Past it the
 *  diagram keeps this size and the figure scrolls, because a legible half of a
 *  diagram beats an illegible whole one — and either way `Expand` is there. */
const MIN_FONT_PX = 9.5;
const BASE_FONT_PX = 13;                    // themeVariables.fontSize, in one place
const MIN_SCALE = MIN_FONT_PX / BASE_FONT_PX;

/** Fit to the space, but never past legibility. */
function size(f: HTMLElement) {
  const svg = f.querySelector("svg");
  if (!svg) return;
  const natural = svg.viewBox.baseVal?.width || 0;
  // The figure is already as wide as the layout allows: the 72ch measure sits on the
  // prose now rather than on the article, and `figure.mermaid` opts out of it.
  const width = f.clientWidth;
  if (!natural || !width) return;

  const scale = Math.max(width / natural, MIN_SCALE);
  const drawn = Math.min(natural, natural * scale);
  svg.style.width = `${Math.round(drawn)}px`;
  svg.style.maxWidth = "none";
  svg.style.height = "auto";
  // Only says so when it is true, so the hint means something when it appears.
  f.dataset.clipped = drawn > width ? "yes" : "no";
}

/** Open it properly: the whole window instead of a column between two sidebars.
 *
 *  `<dialog>` rather than a div with a high z-index, because the top layer, the
 *  backdrop, Escape, and returning focus where it came from are all things it does
 *  and all things worth not reimplementing. */
function addExpander(f: HTMLElement) {
  if (f.querySelector(".mermaid-expand")) return;

  const button = document.createElement("button");
  button.className = "mermaid-expand";
  button.type = "button";
  button.textContent = "Expand";
  button.setAttribute("aria-label", "Open this diagram at full size");
  button.addEventListener("click", () => open(f));
  f.appendChild(button);

  // The figure itself opens it too. The button is the discoverable affordance; the
  // whole surface is what anyone actually clicks at.
  f.addEventListener("click", (e) => {
    if ((e.target as HTMLElement).closest(".mermaid-expand")) return;
    open(f);
  });
}

/** One dialog, reused.
 *
 *  The first version made a fresh `<dialog>` per open and removed it on the `close`
 *  event. That leaks, and it leaks for a reason worth writing down: in at least one
 *  Chromium, `close()` sets `open` to false and never fires `close`, so the listener
 *  holding the cleanup never runs and every expand leaves an element behind.
 *
 *  Depending on an event to tidy up after a thing is fragile even where the event is
 *  reliable. One element that is shown and hidden cannot accumulate however the
 *  browser behaves, and dismissal does not have to be observed to be correct. */
let dialog: HTMLDialogElement | null = null;

function theDialog(): HTMLDialogElement {
  if (dialog) return dialog;

  const d = document.createElement("dialog");
  d.className = "mermaid-dialog";

  const close = document.createElement("button");
  close.type = "button";
  close.className = "mermaid-close";
  close.textContent = "Close";
  close.addEventListener("click", dismiss);

  const stage = document.createElement("div");
  stage.className = "mermaid-stage";

  d.append(close, stage);

  // The dialog itself is the backdrop: a click that lands on it rather than on
  // anything inside it is a click outside the diagram.
  d.addEventListener("click", (e) => {
    if (e.target === d) dismiss();
  });
  // Escape is left to the browser. Cancelling `cancel` to run our own dismissal
  // looked tidier and broke it: the default action is the part that reliably closes
  // a modal here, and the events around it are the part that does not. The stage is
  // emptied on the next open regardless, so nothing is held but one diagram.
  d.addEventListener("close", () => {
    d.querySelector(".mermaid-stage")?.replaceChildren();
  });

  document.body.appendChild(d);
  dialog = d;
  return d;
}

function dismiss() {
  if (!dialog) return;
  if (dialog.open) dialog.close();
  // Drop the copy: an SVG of a few hundred nodes is not worth keeping resident for
  // a dialog that may never be opened again.
  dialog.querySelector(".mermaid-stage")?.replaceChildren();
}

function open(f: HTMLElement) {
  const svg = f.querySelector("svg");
  if (!svg) return;

  const d = theDialog();
  const stage = d.querySelector(".mermaid-stage");
  if (!stage) return;

  const copy = svg.cloneNode(true) as SVGElement;
  // At full size, not the size it was fitted to on the page. This is the whole point
  // of the dialog: the window is two and a half times the column.
  const natural = (svg as SVGSVGElement).viewBox.baseVal?.width || 0;
  copy.setAttribute(
    "style",
    natural ? `width:${Math.round(natural)}px;max-width:none;height:auto`
            : "max-width:none;height:auto",
  );
  stage.replaceChildren(copy);
  if (!d.open) d.showModal();
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
