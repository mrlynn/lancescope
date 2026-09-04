/** A sprite, drawn as SVG rectangles.
 *
 *  Runs of the same colour on a row become one `<rect>` rather than one per pixel — the
 *  castle is 24×16 and would be 384 nodes drawn naively, and is 58 drawn this way. With
 *  `shape-rendering: crispEdges` the result is sharp at any scale, which a bitmap would not
 *  be, and every colour is a custom property, so the art follows the theme like everything
 *  else on screen.
 */

import { PALETTE, SPRITES, type Sprite, type SpriteId } from "./pixels";

type Run = { x: number; y: number; w: number; fill: string };

/** Horizontal run-length encoding of a sprite, computed once per render. Cheap enough that
 *  memoising it would cost more than it saved. */
function runs(sprite: Sprite): Run[] {
  const out: Run[] = [];
  sprite.rows.forEach((row, y) => {
    let x = 0;
    while (x < row.length) {
      const ch = row[x];
      let n = 1;
      while (x + n < row.length && row[x + n] === ch) n++;
      const fill = PALETTE[ch];
      if (fill) out.push({ x, y, w: n, fill });
      x += n;
    }
  });
  return out;
}

export default function Pixel({
  id, scale = 4, title, className,
}: {
  id: SpriteId;
  /** Pixels per pixel. Four is the size these were drawn to be read at. */
  scale?: number;
  title?: string;
  className?: string;
}) {
  const sprite = SPRITES[id];
  return (
    <svg
      className={className}
      width={sprite.w * scale}
      height={sprite.h * scale}
      viewBox={`0 0 ${sprite.w} ${sprite.h}`}
      shapeRendering="crispEdges"
      role={title ? "img" : "presentation"}
      aria-label={title}
      style={{ display: "block", flexShrink: 0, imageRendering: "pixelated" }}
    >
      {runs(sprite).map((r, i) => (
        <rect key={i} x={r.x} y={r.y} width={r.w} height={1} fill={r.fill} />
      ))}
    </svg>
  );
}
