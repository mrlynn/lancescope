/** What the console's centre pane can show, as data rather than as markup.
 *
 *  Extracted from the workspace page because it was not only the workspace page that
 *  needed it. The command palette kept its own copy of the screen list and that copy
 *  went stale the day Operations was added — the palette could not reach it at all,
 *  which for the one control whose entire job is reaching things is the worst place
 *  for a list to drift. Now there is one list and both read it.
 *
 *  No JSX and no React here, so the palette in `components/` can import it without
 *  reaching into a route module.
 */

import type { IconName } from "@/app/components/Icon";

export type Section =
  | "schema" | "versions" | "indices" | "fragments" | "plans" | "ask";
export type Screen =
  | "table" | "query" | "compare" | "training" | "data" | "operations";
export type Tab = Section | "query" | "compare" | "training" | "data";

// Six screens, and two of them have sections.
//
// The old strip was nine peers in one row, and it took a `ResizeObserver`, a
// hysteresis band, two fade masks and a pair of nudge arrows to keep them there.
// They were never nine of the same thing. Four of them read the table's own
// metadata — its shape, its history, its indices, its layout — and are four views
// of one document. The rest each ask the table something, and cost something
// different to ask.
//
// So the four become sections of a Table screen, which is the case the strip was
// always fine for: tightly related facts about one thing. The others become places.
//
// Operations is the second screen with sections, by the same test rather than to
// save a button. Its two are one workflow seen from either end: `plans` is what the
// console worked out should be done, and `ask` is where you say it in words — and
// what the asking produces *is* a plan, rendered by the section next to it. They
// were briefly two screens, which put the answer one navigation away from the
// question that made it.
//
// `plans` comes first because it needs no model. The planners are arithmetic over
// metadata, so a console with nothing configured opens this screen on something that
// works rather than on a panel asking for a key.
export const SECTIONS: Partial<Record<Screen, { id: Section; icon: IconName }[]>> = {
  table: [
    { id: "schema", icon: "schema" },
    { id: "versions", icon: "history" },
    { id: "indices", icon: "index" },
    { id: "fragments", icon: "fragments" },
  ],
  operations: [
    { id: "plans", icon: "check" },
    { id: "ask", icon: "spark" },
  ],
};

/** Which screen a section belongs to. Derived from `SECTIONS` so the two cannot
 *  disagree — a section added above and forgotten here would be a tab that renders
 *  and cannot be navigated to. */
const SCREEN_OF = Object.fromEntries(
  Object.entries(SECTIONS).flatMap(
    ([screen, sections]) => (sections ?? []).map((s) => [s.id, screen as Screen]),
  ),
) as Record<Section, Screen>;

export const SCREENS: { id: Screen; icon: IconName }[] = [
  { id: "table", icon: "table" },
  { id: "query", icon: "search" },
  { id: "compare", icon: "history" },
  { id: "training", icon: "play" },
  // Last but one, and on purpose: it is the only screen that reads columns, and the
  // only one that spends more than kilobytes. Nothing on it runs until somebody
  // presses a button.
  { id: "data", icon: "rows" },
  // And then the only screen about changing the table, which it does by handing over
  // a document and a command rather than by running one.
  { id: "operations", icon: "settings" },
];

export const TABS: Tab[] = ["schema", "versions", "indices", "fragments", "plans", "ask",
                     "query", "compare", "training", "data"];

/** Which screen a view belongs to. The URL still names the view, not the screen —
 *  see `MERGED_TABS`. */
export function screenOf(tab: Tab): Screen {
  return SCREEN_OF[tab as Section] ?? (tab as Screen);
}

/** Where a screen opens. A screen with sections opens at its first one; a screen
 *  without them is the view itself. */
export function entryOf(screen: Screen): Tab {
  return SECTIONS[screen]?.[0]?.id ?? (screen as Tab);
}


/** What a screen is called in prose — for the command palette, which lists them as
 *  sentences rather than as tabs. The tab strip uses the id itself, uppercased by
 *  CSS, because a tab is a label and a palette row is a sentence. */
export const SCREEN_LABEL: Record<Screen, string> = {
  table: "Table",
  query: "Query",
  compare: "Compare",
  training: "Training",
  data: "Data",
  operations: "Operations",
};
