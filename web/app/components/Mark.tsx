/**
 * The LanceScope mark, drawn as geometry rather than shipped as a bitmap: it
 * inherits `currentColor`, so the same component works haze in the header, rule in
 * an empty state, and bright on a hover.
 *
 * The lattice is LanceDB's, cell for cell — same 4x4 grid on a 204pt box, same
 * axis, same radius, redrawn from brand/LanceDB. The glass over cell [3,3] is
 * ours. Source and the reasoning behind it: brand/lancescope/mark.svg.
 *
 * `mono` drops the accent and draws everything in `currentColor`. Use it where a
 * coral glass would be the loudest thing on screen for no reason — empty states,
 * disabled rows — and use it as a test: a mark that is illegible in one colour at
 * 16px has become the LanceDB mark again.
 */
const DOTS: Array<[number, number]> = [
  [0, 0], [1, 0], [2, 0],
  [0, 1], [2, 1], [3, 1],
  [0, 2], [1, 2], [2, 2],
  [1, 3],
];

const AXIS = [48.5, 84.2, 119.8, 155.0];

/** Cell [3,3]. Its neighbours are empty in the original, so the handle has room. */
const LENS = 155.0;

export default function Mark({
  size = 18,
  className = "",
  mono = false,
}: {
  size?: number;
  className?: string;
  mono?: boolean;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 204 204"
      className={className}
      role="img"
      aria-label="LanceScope"
    >
      <g fill="currentColor" opacity={mono ? 1 : 0.55}>
        {DOTS.map(([c, r]) => (
          <circle key={`${c}-${r}`} cx={AXIS[c]} cy={AXIS[r]} r={18.5} />
        ))}
      </g>
      <g
        fill="none"
        strokeWidth={11}
        stroke={mono ? "currentColor" : "var(--video)"}
      >
        <circle cx={LENS} cy={LENS} r={22} />
        <line x1={172} y1={172} x2={191.8} y2={191.8} strokeLinecap="round" />
      </g>
    </svg>
  );
}
