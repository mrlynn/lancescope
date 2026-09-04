/** A shield, drawn from the arms data. No library and no sprites — sixteen devices in
 *  about a hundred lines of path, because the whole point is that the picture and the
 *  blazon come from the same three fields and cannot drift apart.
 *
 *  Sized in a 40×48 box and scaled by CSS, so the same component is a 14px pip in a field
 *  of six hundred and a 64px crest on the warrant.
 */

import { DEVICE, TINCTURE, type Arms, type DeviceId } from "./arms";

const OUTLINE = "M2 2 H38 V21 C38 32.5 29 41 20 46 C11 41 2 32.5 2 21 Z";

/** Three charges sit two above one; a single charge sits in the middle. */
const TRIPLE: [number, number][] = [[13, 15], [27, 15], [20, 30]];
const SINGLE: [number, number][] = [[20, 21]];

function charge(d: DeviceId, x: number, y: number, fill: string, i: number) {
  const k = `${d}${i}`;
  switch (d) {
    case "martlets":
      // A bird in profile, no legs — which is what a martlet is.
      return (
        <path key={k} fill={fill} transform={`translate(${x} ${y}) scale(.9)`}
              d="M-1 -4 q3 -1 4 1 q2 -1 3 0 q-1 1 -2 1 q1 3 -2 5 q-3 2 -5 0 q-3 -1 -3 -3 q2 1 3 0 q-1 -2 2 -4 Z" />
      );
    case "mullets":
      return (
        <path key={k} fill={fill}
              transform={`translate(${x} ${y}) scale(.62)`}
              d="M0 -8 L2.4 -2.6 L8 -2.4 L3.6 1.2 L5.2 7 L0 3.6 L-5.2 7 L-3.6 1.2 L-8 -2.4 L-2.4 -2.6 Z" />
      );
    case "escallops":
      return (
        <g key={k} transform={`translate(${x} ${y}) scale(.85)`} fill={fill}>
          <path d="M-5 3 A5 5.6 0 0 1 5 3 L4 4.4 L2.4 3.2 L0.8 4.6 L-0.8 3.2 L-2.4 4.6 L-4 3.2 Z" />
          <rect x="-1.2" y="3.6" width="2.4" height="1.6" rx=".6" />
        </g>
      );
    case "towers":
      return (
        <g key={k} transform={`translate(${x} ${y}) scale(.85)`} fill={fill}>
          <rect x="-3.6" y="-2" width="7.2" height="7" />
          <rect x="-3.6" y="-4.4" width="1.9" height="2.6" />
          <rect x="-0.95" y="-4.4" width="1.9" height="2.6" />
          <rect x="1.7" y="-4.4" width="1.9" height="2.6" />
        </g>
      );
    case "roses":
      return (
        <g key={k} transform={`translate(${x} ${y}) scale(.8)`} fill={fill}>
          {[0, 72, 144, 216, 288].map((a) => (
            <circle key={a} r="2.2"
                    cx={Math.sin((a * Math.PI) / 180) * 3}
                    cy={-Math.cos((a * Math.PI) / 180) * 3} />
          ))}
        </g>
      );
    case "lozenges":
      return <path key={k} fill={fill} d={`M${x} ${y - 5} L${x + 3.4} ${y} L${x} ${y + 5} L${x - 3.4} ${y} Z`} />;
    case "annulets":
      return <circle key={k} cx={x} cy={y} r="3.4" fill="none" stroke={fill} strokeWidth="1.7" />;
    case "crescent":
      // Two arcs: the moon is the difference between them.
      return (
        <path key={k} fill={fill} transform={`translate(${x} ${y})`}
              d="M-6 -1 A6 6 0 1 0 3.2 -4.6 A7.2 7.2 0 1 1 -6 -1 Z" />
      );
    default:
      return null;
  }
}

function ordinary(d: DeviceId, fill: string) {
  switch (d) {
    case "fess":    return <rect x="0" y="19" width="40" height="9" fill={fill} />;
    case "pale":    return <rect x="15.5" y="0" width="9" height="48" fill={fill} />;
    case "chief":   return <rect x="0" y="0" width="40" height="12" fill={fill} />;
    case "cross":   return (<><rect x="0" y="18" width="40" height="9" fill={fill} />
                             <rect x="15.5" y="0" width="9" height="48" fill={fill} /></>);
    case "bend":    return <path d="M-4 2 L36 46 L44 40 L4 -4 Z" fill={fill} />;
    case "saltire": return (<><path d="M-4 2 L36 46 L44 40 L4 -4 Z" fill={fill} />
                             <path d="M44 2 L4 46 L-4 40 L36 -4 Z" fill={fill} /></>);
    case "chevron": return <path d="M20 13 L42 38 L42 47 L20 22 L-2 47 L-2 38 Z" fill={fill} />;
    // A bordure is the outline itself, drawn thick and clipped so only the inner half shows.
    case "bordure": return <path d={OUTLINE} fill="none" stroke={fill} strokeWidth="7" />;
    default:        return null;
  }
}

export default function Shield({
  arms, size = 40, title,
}: { arms: Arms; size?: number; title?: string }) {
  const field = TINCTURE[arms.field].hex;
  const fill = TINCTURE[arms.charge].hex;
  const id = `sh-${arms.field}-${arms.device}-${arms.charge}`;
  const isCharge = DEVICE[arms.device].blazon.startsWith("three")
                   || arms.device === "crescent";
  const spots = arms.device === "crescent" ? SINGLE : TRIPLE;

  return (
    <svg width={size} height={size * 1.2} viewBox="0 0 40 48"
         role={title ? "img" : "presentation"} aria-label={title}
         style={{ display: "block", flexShrink: 0 }}>
      <defs>
        <clipPath id={id}><path d={OUTLINE} /></clipPath>
      </defs>
      <g clipPath={`url(#${id})`}>
        <path d={OUTLINE} fill={field} />
        {isCharge
          ? spots.map(([x, y], i) => charge(arms.device, x, y, fill, i))
          : ordinary(arms.device, fill)}
      </g>
      {/* The rim, in the console's own hairline, so a silver shield still has an edge on a
          light ground and a sable one has one on a dark. */}
      <path d={OUTLINE} fill="none" stroke="var(--rule)" strokeWidth="1.4" />
    </svg>
  );
}
