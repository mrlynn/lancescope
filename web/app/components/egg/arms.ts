/** Heraldry, as data rather than as a string.
 *
 *  A blazon written out is opaque — "azure, three martlets or" tells you nothing unless you
 *  already know that azure is blue, or is gold, and a martlet is a bird. So the arms are
 *  stored as three fields, the words are generated from them, and so is the drawing. Print
 *  the two side by side and the vocabulary teaches itself: nobody has to be told what azure
 *  means once they have seen it next to a blue shield.
 *
 *  It also gives the wide column something to be. Reading a blazon costs thirty times what
 *  a house costs, and it should feel like it bought something.
 */

export type TinctureId =
  | "or" | "argent" | "gules" | "azure" | "sable" | "vert" | "purpure";

export const TINCTURE: Record<TinctureId, {
  plain: string; hex: string; metal: boolean;
}> = {
  // The two metals and the five colours. The distinction is not decoration: heraldry does
  // not put colour on colour, and obeying that is why these shields read at 14 pixels.
  or:      { plain: "gold",   hex: "#c9a227", metal: true },
  argent:  { plain: "silver", hex: "#e7e3db", metal: true },
  gules:   { plain: "red",    hex: "#a5342a", metal: false },
  azure:   { plain: "blue",   hex: "#31558c", metal: false },
  sable:   { plain: "black",  hex: "#241f1c", metal: false },
  vert:    { plain: "green",  hex: "#31694a", metal: false },
  purpure: { plain: "purple", hex: "#6b3f7a", metal: false },
};

export type DeviceId =
  | "fess" | "pale" | "bend" | "chevron" | "cross" | "saltire" | "chief" | "bordure"
  | "martlets" | "mullets" | "escallops" | "towers" | "roses" | "lozenges" | "annulets"
  | "crescent";

export const DEVICE: Record<DeviceId, { blazon: string; plain: string }> = {
  fess:      { blazon: "a fess",            plain: "a band across the middle" },
  pale:      { blazon: "a pale",            plain: "a band down the middle" },
  bend:      { blazon: "a bend",            plain: "a diagonal band" },
  chevron:   { blazon: "a chevron",         plain: "an inverted V" },
  cross:     { blazon: "a cross",           plain: "a cross" },
  saltire:   { blazon: "a saltire",         plain: "a diagonal cross, like an X" },
  chief:     { blazon: "a chief",           plain: "a band across the top" },
  bordure:   { blazon: "a bordure",         plain: "a border round the edge" },
  martlets:  { blazon: "three martlets",    plain: "three small birds" },
  mullets:   { blazon: "three mullets",     plain: "three stars" },
  escallops: { blazon: "three escallops",   plain: "three scallop shells" },
  towers:    { blazon: "three towers",      plain: "three towers" },
  roses:     { blazon: "three roses",       plain: "three roses" },
  lozenges:  { blazon: "three lozenges",    plain: "three diamonds" },
  annulets:  { blazon: "three annulets",    plain: "three rings" },
  crescent:  { blazon: "a crescent",        plain: "a crescent moon" },
};

export type Arms = { field: TinctureId; device: DeviceId; charge: TinctureId };

/** The words a herald would say. Generated, so the drawing and the blazon cannot disagree. */
export const blazonOf = (a: Arms) =>
  `${a.field}, ${DEVICE[a.device].blazon} ${a.charge}`;

/** The same thing in English, which is the part that makes the other part learnable. */
export const plainOf = (a: Arms) =>
  `a ${TINCTURE[a.field].plain} shield with ${DEVICE[a.device].plain} in ${TINCTURE[a.charge].plain}`;

export const sameArms = (a: Arms, b: Arms) =>
  a.field === b.field && a.device === b.device && a.charge === b.charge;

const TINCTURES = Object.keys(TINCTURE) as TinctureId[];
const DEVICES = Object.keys(DEVICE) as DeviceId[];

/** Every combination the rule of tincture allows, shuffled. A deck rather than a random
 *  draw, so a roll never issues the same arms to two different knights by accident — the
 *  blazon has to be a real predicate or the filter that reads it is lying. */
export function deck(n: number): Arms[] {
  const all: Arms[] = [];
  for (const field of TINCTURES) {
    for (const charge of TINCTURES) {
      if (TINCTURE[field].metal === TINCTURE[charge].metal) continue;
      for (const device of DEVICES) all.push({ field, device, charge });
    }
  }
  for (let i = all.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [all[i], all[j]] = [all[j], all[i]];
  }
  return all.slice(0, n);
}
