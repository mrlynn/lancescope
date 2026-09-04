/** The Squire's Tour — seven chapters, and what they cost to walk.
 *
 *  All the state there is: which chapter, which steps have been taken in it, the running
 *  scale, and the words earned so far. No score and no failure state, because a lesson with
 *  a score is an exam, and the diagnosis game that used to follow this one was exactly that
 *  — it asked the player to do the job the console's own findings already do.
 *
 *  The chapters themselves are data. See ./chapters.
 */

import { CHAPTERS, type Chapter, type Step, type Term } from "./chapters";

export { CHAPTERS };
export type { Chapter, Step, Term };

/* --------------------------------------------------------------------- the tour ---- */

export type Tour = {
  /** Which chapter, 0-based. */
  at: number;
  /** Steps taken in the current chapter, by index. */
  taken: number[];
  /** Whether the failing first step of chapter I has revealed the rest. */
  revealed: boolean;
  /** The running scale, carried the whole way. */
  scale: number;
  /** Terms defined so far, in the order they were earned. */
  learned: Term[];
  /** What the last step said. */
  said: string;
  /** Chapter finished, fact showing, waiting to move on. */
  done: boolean;
  over: boolean;
};

export const startTour = (): Tour => ({
  at: 0, taken: [], revealed: false, scale: 0, learned: [], said: "", done: false, over: false,
});

export const chapter = (t: Tour) => CHAPTERS[Math.min(t.at, CHAPTERS.length - 1)];

/** Which steps a player may take right now. Chapter I hides everything but the failing
 *  first step until it has failed, because the lesson is that the instinct is wrong. */
export function offered(t: Tour): number[] {
  const c = chapter(t);
  const all = c.steps.map((_, i) => i);
  const hasReveal = c.steps.some((s) => s.reveals);
  if (hasReveal && !t.revealed) return all.filter((i) => c.steps[i].reveals);
  return all.filter((i) => !c.steps[i].reveals && !t.taken.includes(i));
}

export function take(t: Tour, i: number) {
  if (t.done || t.over) return;
  const c = chapter(t);
  const step = c.steps[i];
  if (!step || !offered(t).includes(i)) return;

  t.said = step.says;
  if (step.reveals) { t.revealed = true; return; }

  t.taken.push(i);
  t.scale += step.cost;
  if (t.taken.length >= c.needs) {
    t.done = true;
    // The term is earned by finishing the chapter, not by entering it.
    if (!t.learned.some((x) => x.word === c.term.word)) t.learned.push(c.term);
  }
}

export function onward(t: Tour) {
  if (!t.done) return;
  if (t.at + 1 >= CHAPTERS.length) { t.over = true; return; }
  t.at++;
  t.taken = [];
  t.revealed = false;
  t.said = "";
  t.done = false;
}

/** Did any step in the chapter just taken draw the arms? */
export const armsShown = (t: Tour) =>
  t.taken.some((i) => chapter(t).steps[i]?.showsArms);

export const RIDES = 5;

/** What the roll is made of. Unchanged from the schema the console prints, because the
 *  column widths are how a player tells a probe from a scan. */
export const COL = {
  id:       { label: "id",       type: "int64",                     bytes: 8,
              says: "his number on the roll" },
  renown:   { label: "renown",   type: "float32",                   bytes: 4,
              says: "what the heralds rate him, nought to one" },
  house:    { label: "house",    type: "dict<string>",              bytes: 12,
              says: "the banner he rides under — one of eight, so it stores as a code" },
  blazon:   { label: "blazon",   type: "string",                    bytes: 120,
              says: "his arms, written out the way a herald would say them" },
  likeness: { label: "likeness", type: "fixed_size_list<f32,1536>", bytes: 1536 * 4,
              says: "1,536 numbers derived from his portrait, so two faces can be compared "
                  + "without looking at either" },
  portrait: { label: "portrait", type: "blob",                      bytes: 2_516_582,
              says: "the painted panel itself. This column is the reason a roll weighs what "
                  + "it weighs" },
} as const;

export const ROW_BYTES = Object.values(COL).reduce((a, c) => a + c.bytes, 0);
export const TABLE = "the Roll of the Realm";

export type Difficulty = "squire" | "knight" | "champion";
export const ORDER: Difficulty[] = ["squire", "knight", "champion"];

export const DIFFICULTIES: Record<Difficulty, {
  label: string; blurb: string;
  /** How many candidate causes are offered. Fewer is easier. */
  choices: number;
  /** Squire is told what the access path means without being asked. */
  explainPath: boolean;
}> = {
  squire: {
    label: "Squire",
    blurb: "Three causes to choose between, and the access path is explained to you.",
    choices: 3, explainPath: true,
  },
  knight: {
    label: "Knight",
    blurb: "Four causes, and you are left to know what the access path means.",
    choices: 4, explainPath: false,
  },
  champion: {
    label: "Champion",
    blurb: "Four causes, no explanation, and no second guess at the cause.",
    choices: 4, explainPath: false,
  },
};

export function format(b: number) {
  if (b < 1024) return `${Math.round(b)} B`;
  if (b < 1024 ** 2) return `${(b / 1024).toFixed(1)} KB`;
  if (b < 1024 ** 3) return `${(b / 1024 ** 2).toFixed(1)} MB`;
  return `${(b / 1024 ** 3).toFixed(2)} GB`;
}
