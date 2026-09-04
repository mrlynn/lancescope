/** Eight-bit sprites, authored as character grids.
 *
 *  No image files, no sprite sheet, no build step: each sprite is rows of characters, one
 *  per pixel, and the renderer turns runs of them into `<rect>`s. That makes the art
 *  diffable, editable by hand, sharp at any scale, and themed by the same custom properties
 *  as the rest of the console — none of which is true of a PNG.
 *
 *  Twelve colours, and every sprite draws from the same one, the way a machine with a single
 *  hardware palette would. Keeping to it is what makes eight separate drawings look like one
 *  game rather than eight pieces of clip art.
 *
 *      .  transparent      4  metal, dark      8  gold
 *      0  outline          5  metal, light     9  wood
 *      1  stone, dark      6  parchment/skin   a  green
 *      2  stone            7  red cloth        b  white
 *      3  stone, light
 */

export type Sprite = { w: number; h: number; rows: string[] };

const s = (rows: string[]): Sprite => ({ w: rows[0].length, h: rows.length, rows });


/** Lancelot, standing, in a red tabard. The plume is the one flash of gold, so the eye
 *  finds him in a scene before it finds anything else. */
export const KNIGHT = s([
  "..........88....",
  "..........888...",
  "....000000008...",
  "....05555550....",
  "....05555550....",
  "....00500500....",
  "....00500500....",
  "....05555550....",
  "....05555550....",
  "....00000000....",
  "...0000000000...",
  "...0000000000...",
  "...5500000055...",
  "...5577777755...",
  "...5577777755...",
  "...5577777755...",
  "....88888888....",
  ".....44..44.....",
  ".....44..44.....",
  ".....00..00.....",
]);

/** The vault. One castle, because the whole pitch of chapter II is that it is one. */
export const CASTLE = s([
  "......................",
  ".2.2.2..........2.2.2.",
  ".22222..........22222.",
  ".22222..........22222.",
  ".21112..........21112.",
  ".22222..........22222.",
  ".222222.2.2.2.2.22222.",
  ".22222222222222222222.",
  ".22222222222222222222.",
  ".22222222222222222222.",
  ".22222222222222222222.",
  ".22222229999992222222.",
  ".22222229000092222222.",
  ".22222229000092222222.",
  ".22222229000092222222.",
  ".00000000000000000000.",
]);

/** One of the four buildings other realms keep. Small, separate, and needing a runner. */
export const HUT = s([
  "..........",
  "....00....",
  "...0770...",
  "..077770..",
  ".07777770.",
  "0777777770",
  "0022222200",
  ".02010200.",
  ".02010200.",
  ".00000000.",
]);

/** A crate in the cellar, with the tag that is all the ledger actually holds. */
export const CRATE = s([
  "......................",
  ".................00000",
  ".................0b0b0",
  ".................0bbb0",
  "..00000000000000000000",
  "..0099999999999000....",
  "..0900999999990090....",
  "..0999099999909990....",
  "..0999900990099990....",
  "..0000000000000000....",
  "..0999999009999990....",
  "..0999900990099990....",
  "..0999099999909990....",
  "..0900999999990090....",
  "..0099999999999900....",
  "..0000000000000000....",
  "......................",
  "......................",
]);

/** The scale. Everything Lancelot carries is weighed on the way out. */
export const SCALES = s([
  "......000.......",
  "......050.......",
  "0000000500000000",
  "0.....050......0",
  "0.....050......0",
  "0.....050......0",
  "00000.050..00000",
  "08880.050..08880",
  "00000.050..00000",
  "......050.......",
  "......050.......",
  "....0005000.....",
  "...0555555550...",
  "..055555555550..",
  "..000000000000..",
  "................",
]);

/** A painting in its frame: the thing you must ask for by name before it will move. */
export const PAINTING = s([
  "000000000000000000",
  "088888888888888880",
  "080000000000000080",
  "080aaaaaaaa77aa080",
  "080aaa000000aaa080",
  "080aaa055550aaa080",
  "080aaa000000aaa080",
  "080aaa055550aaa080",
  "080aaa055550aaa080",
  "080aaa000000aaa080",
  "080a0000000000a080",
  "080a0777777770a080",
  "080000000000000080",
  "088888888888888880",
  "000000000000000000",
  "....99......99....",
  "....99......99....",
  "....99......99....",
]);

/** The oracle: shown every likeness once, and remembering roughly where each one sits. */
export const ORACLE = s([
  "..................",
  ".....00000000.....",
  "....00bbbbbb00....",
  "...0bbbbbbbbbb0...",
  "..00bbbbbbbbbb00..",
  "..0bbbb5555bbbb0..",
  "..0bbb558855bbb0..",
  "..0bbb580085bbb0..",
  "..0bbb580085bbb0..",
  "..0bbb558855bbb0..",
  "..0bbbb5555bbbb0..",
  "..00bbbbbbbbbb00..",
  "...0bbbbbbbbbb0...",
  "....00bbbbbb00....",
  ".....00000000.....",
  ".....09999990.....",
  "...000000000000...",
  "...000000000000...",
]);

/** A column, as the vault keeps them: one ledger, ruled, with writing on it. */
export const SCROLL = s([
  "..............",
  "..0000000000..",
  "..0999999990..",
  "..0000000000..",
  "..0bbbbbbbb0..",
  "..0b111111b0..",
  "..0bbbbbbbb0..",
  "..0b1111bbb0..",
  "..0bbbbbbbb0..",
  "..0b111111b0..",
  "..0bbbbbbbb0..",
  "..0b11bbbbb0..",
  "..0bbbbbbbb0..",
  "..0b11111bb0..",
  "..0000000000..",
  "..0999999990..",
  "..0000000000..",
  "..............",
]);

/** A runner, forever carrying messages between four buildings, out of date before they
 *  arrive. The joke of chapter II. */
export const RUNNER = s([
  "......",
  "..00..",
  ".0660.",
  "..00..",
  ".0770.",
  "07770.",
  ".070..",
  ".0.0..",
  "00.00.",
]);

export const SPRITES = { knight: KNIGHT, castle: CASTLE, hut: HUT, crate: CRATE, scales: SCALES, painting: PAINTING, oracle: ORACLE, scroll: SCROLL, runner: RUNNER } as const;

export type SpriteId = keyof typeof SPRITES;

/** Every character a sprite may use, mapped to the custom property that colours it. */
export const PALETTE: Record<string, string> = {
  "0": "var(--px-0)", "1": "var(--px-1)", "2": "var(--px-2)", "3": "var(--px-3)",
  "4": "var(--px-4)", "5": "var(--px-5)", "6": "var(--px-6)", "7": "var(--px-7)",
  "8": "var(--px-8)", "9": "var(--px-9)", a: "var(--px-a)", b: "var(--px-b)",
};
