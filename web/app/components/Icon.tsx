/**
 * The icon set.
 *
 * One family, drawn to the same rules as the LanceDB mark: a 20-unit grid, circles
 * on the mark's own radius where a dot will do, 1.5 stroke everywhere else, round
 * caps and joins. Nothing here is imported — an icon font or a third-party pack
 * would bring its own line weight and corner radius and the header would stop
 * looking like one drawing.
 *
 * Every glyph paints `currentColor` and inherits its size, so the same `<Icon>`
 * works haze in a rail, coral on a selected tab, and bright on hover without a
 * variant per context.
 */

export type IconName =
  | "database" | "table" | "search" | "settings" | "sun" | "moon" | "system" | "play"
  | "schema" | "history" | "index" | "fragments" | "rows"
  | "chevronDown" | "chevronRight" | "chevronLeft" | "arrowRight"
  | "plus" | "check" | "close" | "trash" | "refresh" | "external"
  | "star" | "starFilled" | "clock" | "spark" | "warning" | "info" | "back";

/** Paths are authored on a 20x20 grid. Stroke-only unless the glyph reads better
 *  solid, in which case the entry carries its own `fill`. */
const PATHS: Record<IconName, React.ReactNode> = {
  // A stack of manifests, not the usual three ellipses: Lance's unit is a versioned
  // table, and the dot marks the one that is current.
  database: (
    <>
      <ellipse cx="10" cy="5.25" rx="6" ry="2.5" />
      <path d="M4 5.25v9.5c0 1.38 2.69 2.5 6 2.5s6-1.12 6-2.5V5.25" />
      <path d="M4 10c0 1.38 2.69 2.5 6 2.5s6-1.12 6-2.5" />
    </>
  ),
  table: (
    <>
      <rect x="3" y="4" width="14" height="12" rx="1.5" />
      <path d="M3 8h14M8 8v8" />
    </>
  ),
  search: (
    <>
      <circle cx="9" cy="9" r="5" />
      <path d="M12.8 12.8 16.5 16.5" />
    </>
  ),
  // Sliders rather than a gear. A gear at 16px is mush, and the console's settings
  // really are a small set of switched values.
  settings: (
    <>
      <path d="M3 6h4M11 6h6M3 14h6M13 14h4" />
      <circle cx="9" cy="6" r="2" />
      <circle cx="11" cy="14" r="2" />
    </>
  ),
  sun: (
    <>
      <circle cx="10" cy="10" r="3.5" />
      <path d="M10 2.5v1.8M10 15.7v1.8M17.5 10h-1.8M4.3 10H2.5M15.3 4.7l-1.3 1.3M6 14l-1.3 1.3M15.3 15.3 14 14M6 6 4.7 4.7" />
    </>
  ),
  moon: <path d="M15.5 12.4A6.2 6.2 0 0 1 7.6 4.5a6.5 6.5 0 1 0 7.9 7.9Z" />,
  // "Follow the OS" — a screen, because that is literally whose preference it is.
  system: (
    <>
      <rect x="2.5" y="4" width="15" height="9.5" rx="1.5" />
      <path d="M7 16.5h6" />
    </>
  ),
  play: (
    <>
      <circle cx="10" cy="10" r="7" />
      <path d="M8.5 7.2 13 10l-4.5 2.8Z" />
    </>
  ),

  // The five console tabs. Each says what the tab reads, so the row is scannable
  // before the labels are.
  schema: (
    <>
      <path d="M3.5 5h13M3.5 10h13M3.5 15h13" />
      <circle cx="6.5" cy="5" r="1.4" fill="currentColor" stroke="none" />
      <circle cx="11" cy="10" r="1.4" fill="currentColor" stroke="none" />
      <circle cx="8" cy="15" r="1.4" fill="currentColor" stroke="none" />
    </>
  ),
  history: (
    <>
      <path d="M3.6 8.4A7 7 0 1 1 3 10.6" />
      <path d="M3.2 4.6v3.9h3.9" />
      <path d="M10 6.4V10l2.6 1.6" />
    </>
  ),
  // An index is a centroid and the points that resolve to it — which is literally
  // what an IVF index is, and reads at 14px where a crosshair just reads as a plus.
  index: (
    <>
      <circle cx="10" cy="10" r="6.5" />
      <circle cx="10" cy="10" r="2" fill="currentColor" stroke="none" />
      <circle cx="10" cy="3.5" r="1.5" fill="currentColor" stroke="none" />
      <circle cx="16.5" cy="10" r="1.5" fill="currentColor" stroke="none" />
      <circle cx="6" cy="15.6" r="1.5" fill="currentColor" stroke="none" />
    </>
  ),
  fragments: (
    <>
      <rect x="3" y="3.5" width="5.6" height="5.6" rx="1" />
      <rect x="11.4" y="3.5" width="5.6" height="5.6" rx="1" />
      <rect x="3" y="10.9" width="5.6" height="5.6" rx="1" />
      <rect x="11.4" y="10.9" width="5.6" height="5.6" rx="1" />
    </>
  ),
  rows: (
    <>
      <rect x="3" y="4" width="14" height="12" rx="1.5" />
      <path d="M3 8h14M3 12h14" />
    </>
  ),

  chevronDown: <path d="m5.5 8 4.5 4.5L14.5 8" />,
  chevronRight: <path d="m8 5.5 4.5 4.5L8 14.5" />,
  chevronLeft: <path d="m12 5.5-4.5 4.5L12 14.5" />,
  arrowRight: <path d="M3.5 10h13M11.5 5l5 5-5 5" />,
  back: <path d="M16.5 10h-13M8.5 5l-5 5 5 5" />,

  plus: <path d="M10 4v12M4 10h12" />,
  check: <path d="m4.5 10.5 3.6 3.6L15.5 6.7" />,
  close: <path d="m5.5 5.5 9 9M14.5 5.5l-9 9" />,
  trash: (
    <>
      <path d="M3.5 5.5h13M7 5.5V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v1.5" />
      <path d="M5.5 5.5 6.3 16a1 1 0 0 0 1 .9h5.4a1 1 0 0 0 1-.9l.8-10.5" />
    </>
  ),
  refresh: (
    <>
      <path d="M16.4 8.4A7 7 0 1 0 17 10.6" />
      <path d="M16.8 4.6v3.9h-3.9" />
    </>
  ),
  external: (
    <>
      <path d="M11 3.5h5.5V9" />
      <path d="M16.5 3.5 9 11" />
      <path d="M14.5 11.5v4a1.5 1.5 0 0 1-1.5 1.5H4.5A1.5 1.5 0 0 1 3 15.5V7a1.5 1.5 0 0 1 1.5-1.5h4" />
    </>
  ),

  star: <path d="m10 3.2 2.1 4.3 4.7.7-3.4 3.3.8 4.7-4.2-2.2-4.2 2.2.8-4.7L3.2 8.2l4.7-.7Z" />,
  starFilled: (
    <path
      d="m10 3.2 2.1 4.3 4.7.7-3.4 3.3.8 4.7-4.2-2.2-4.2 2.2.8-4.7L3.2 8.2l4.7-.7Z"
      fill="currentColor"
    />
  ),
  clock: (
    <>
      <circle cx="10" cy="10" r="7" />
      <path d="M10 5.8V10l2.9 1.8" />
    </>
  ),
  // The intelligence layer's glyph: the mark's own dot, with the two smaller
  // satellites that say "derived", not "magic".
  spark: (
    <>
      <path d="M9 3.4 10.3 7l3.6 1.3-3.6 1.3L9 13.2 7.7 9.6 4.1 8.3 7.7 7Z" />
      <path d="M14.6 12.2l.6 1.7 1.7.6-1.7.6-.6 1.7-.6-1.7-1.7-.6 1.7-.6Z" />
    </>
  ),
  warning: (
    <>
      <path d="M10 3.4 17.2 16H2.8Z" />
      <path d="M10 8v3.4" />
      <circle cx="10" cy="13.8" r="0.9" fill="currentColor" stroke="none" />
    </>
  ),
  info: (
    <>
      <circle cx="10" cy="10" r="7" />
      <path d="M10 9.2v4.2" />
      <circle cx="10" cy="6.6" r="0.9" fill="currentColor" stroke="none" />
    </>
  ),
};

export default function Icon({
  name,
  size = 16,
  className = "",
  strokeWidth = 1.5,
}: {
  name: IconName;
  size?: number;
  className?: string;
  strokeWidth?: number;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`shrink-0 ${className}`}
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}
