/** The sequence, matched off a rolling buffer.
 *
 *  A cursor that advances on a hit and resets on a miss is the obvious way to do this
 *  and it is wrong on repeated keys: `↑↑↑↓↓←→←→ba` is a perfectly ordinary thing for
 *  a person to type — an extra tap, a key that repeated — and the cursor version drops
 *  it on the floor, because backing off correctly needs the longest suffix of what was
 *  typed that is also a prefix of the sequence. Keeping the last ten keys and comparing
 *  is that answer without the machinery, and ten strings is not a memory decision.
 *
 *  No DOM and no React in here on purpose: the matcher is the part with the fiddly
 *  edge cases, so it is the part worth being able to read on its own.
 */

export const KONAMI: readonly string[] = [
  "arrowup", "arrowup",
  "arrowdown", "arrowdown",
  "arrowleft", "arrowright",
  "arrowleft", "arrowright",
  "b", "a",
];

/** Every key that can be part of the sequence. Anything else is not a wrong key, it is
 *  not a key: a bare `Shift` or `Meta` keydown, a Tab out and back, an F5, a media key
 *  from the row above the numbers. macOS emits a naked `Meta` keydown just for tabbing
 *  into the window, and treating those as input is what makes a matcher that passes every
 *  test fail for the person actually typing — they push real keys out of the buffer and
 *  the run dies silently, three keys from the end. */
export type Matcher = {
  /** Feed one `KeyboardEvent.key`. True exactly once, on the last key of the sequence. */
  push(key: string): boolean;
  /** How many keys deep the player currently is — 0 means nothing is in progress. */
  depth(): number;
  reset(): void;
};

export function createMatcher(seq: readonly string[] = KONAMI): Matcher {
  const alphabet = new Set(seq);
  let buf: string[] = [];

  const depth = () => {
    // The longest suffix of what was typed that is a prefix of the sequence. That is
    // the honest "how far in are they", and it is what decides whether we are allowed
    // to swallow an arrow key.
    for (let n = Math.min(buf.length, seq.length); n > 0; n--) {
      let ok = true;
      for (let i = 0; i < n; i++) {
        if (buf[buf.length - n + i] !== seq[i]) { ok = false; break; }
      }
      if (ok) return n;
    }
    return 0;
  };

  return {
    push(key: string) {
      const k = key.toLowerCase();
      // Not part of the sequence at all: leave the run exactly as it was.
      if (!alphabet.has(k)) return false;
      buf.push(k);
      if (buf.length > seq.length) buf.shift();
      const hit = buf.length === seq.length && seq.every((s, i) => buf[i] === s);
      if (hit) buf = [];
      return hit;
    },
    depth,
    reset() { buf = []; },
  };
}
