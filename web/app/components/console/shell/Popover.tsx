"use client";

/** A layer that escapes whatever is clipping it.
 *
 *  While the console was a scrolling page, an absolutely-positioned dropdown near
 *  the bottom simply made the page taller. Inside a pane with `overflow-y: auto` it
 *  is clipped by that pane instead — measured on the filter box with its completion
 *  list open: 190px tall, 54px of it past the pane's edge, the last three columns
 *  unreachable. That box is the console's best keyboard affordance and it sits
 *  mid-form in the query workspace, so it is the worst place for this to happen.
 *
 *  Rendered into the document rather than in place, positioned against its anchor,
 *  and flipped above when there is no room below.
 *
 *  Positioned by writing to the node rather than by holding coordinates in state.
 *  A scroll listener that set state would re-render the whole panel for every frame
 *  of a scroll, and the anchor lives in a pane that scrolls — so this measures and
 *  assigns, which is what a layout effect is for.
 */

import { type ReactNode, useCallback, useEffect, useRef, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";

/** Kept clear of the window's own edge, so a panel never sits flush against it. */
const MARGIN = 8;

/** Never changes after the first paint, so there is nothing to subscribe to. */
const subscribeNever = () => () => {};
const onClient = () => true;
const onServer = () => false;

export default function Popover({ anchorRef, open, children, minHeight = 120, stretch = true }: {
  /** The element to hang off — usually the input or button that opened this. A ref
   *  rather than an element, because reading `.current` while rendering is reading a
   *  value that has not been committed yet. */
  anchorRef: { current: HTMLElement | null };
  open: boolean;
  children: ReactNode;
  /** Below this, flipping above is better than scrolling a sliver. */
  minHeight?: number;
  /** Match the anchor's width — right for a completion list under an input, wrong
   *  for a menu that has its own. */
  stretch?: boolean;
}) {
  const box = useRef<HTMLDivElement | null>(null);

  const place = useCallback(() => {
    const node = box.current;
    const anchor = anchorRef.current;
    if (!node || !anchor) return;

    const a = anchor.getBoundingClientRect();
    const below = window.innerHeight - a.bottom - MARGIN;
    const above = a.top - MARGIN;
    // Prefer below, the way a dropdown is read. Flip only when below cannot hold a
    // useful amount and above can hold more.
    const flip = below < minHeight && above > below;

    node.style.left = `${a.left}px`;
    node.style.width = stretch ? `${a.width}px` : "";
    node.style.maxHeight = `${Math.max(80, (flip ? above : below) - 4)}px`;
    // Assigned before the top is read back, because the height it settles at is
    // what decides where the top goes when flipped.
    node.style.top = flip ? `${MARGIN}px` : `${a.bottom + 4}px`;
    if (flip) node.style.top = `${Math.max(MARGIN, a.top - 4 - node.offsetHeight)}px`;
  }, [anchorRef, minHeight, stretch]);

  // Whether there is a document to portal into. This renders inside a static export
  // that is prerendered without one, so the answer differs between the server pass
  // and the client — which is exactly the question `useSyncExternalStore` asks, and
  // the shape three other files in `app/lib` already use for it. An effect setting
  // state would do the same thing and cost a second render to say so.
  const mounted = useSyncExternalStore(subscribeNever, onClient, onServer);

  useEffect(() => {
    if (!open || !mounted) return;
    place();
    // Capture phase: the anchor is inside a pane that scrolls, and a scroll event on
    // that pane does not bubble to the window.
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open, mounted, place]);

  if (!mounted || !open) return null;

  return createPortal(
    <div
      ref={(node) => {
        box.current = node;
        if (node) place();
      }}
      // Above the row drawer's scrim, which is the only thing in this app that
      // deliberately covers the workspace.
      className="fixed z-[70]"
      // Off-screen until placed, so a panel never appears at the top-left corner for
      // the frame before it is measured.
      style={{ top: -9999, left: -9999 }}
    >
      {children}
    </div>,
    document.body,
  );
}
