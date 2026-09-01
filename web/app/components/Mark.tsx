/**
 * The LanceDB mark, redrawn from brand/LanceDB as geometry rather than shipped as
 * a bitmap: it inherits `currentColor`, so the same component works coral in the
 * header, haze in the schema panel, and bright on a hover state.
 *
 * Grid is 4x4 on a 204pt box, 11 of the 16 cells filled — matching the supplied
 * asset cell for cell.
 */
const DOTS: Array<[number, number]> = [
  [0, 0], [1, 0], [2, 0],
  [0, 1], [2, 1], [3, 1],
  [0, 2], [1, 2], [2, 2],
  [1, 3], [3, 3],
];

const AXIS = [48.5, 84.2, 119.8, 155.0];

export default function Mark({ size = 18, className = "" }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 204 204"
      fill="currentColor"
      className={className}
      role="img"
      aria-label="LanceDB"
    >
      {DOTS.map(([c, r]) => (
        <circle key={`${c}-${r}`} cx={AXIS[c]} cy={AXIS[r]} r={18.5} />
      ))}
    </svg>
  );
}
