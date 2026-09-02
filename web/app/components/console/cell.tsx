"use client";

/** How one cell reads in a grid.
 *
 *  Lifted out of `tabs.tsx` so the grid can use it without importing the panels
 *  that use the grid. A cell is a scalar, or one of the summaries the server
 *  substitutes for a column it declined to materialise — and the summaries are
 *  rendered as summaries, never as data that was read. */

import { fmtBytes } from "@/app/lib/api";
import type { Cell } from "@/app/lib/catalog";

export function CellView({ v }: { v: Cell }) {
  if (v === null) return <span className="text-[var(--dim)]">null</span>;
  if (typeof v === "object") {
    if ("blob" in v) {
      const b = fmtBytes(v.size_bytes ?? 0);
      return (
        <span style={{ color: "var(--video)" }} title="described from its Blob V2 descriptor — not read">
          blob {b.value} {b.unit}
        </span>
      );
    }
    if ("vector_dim" in v) {
      return (
        <span className="text-[var(--haze)]" title={v.head.join(", ")}>
          [{v.head.slice(0, 3).map((n) => n.toFixed(3)).join(", ")}, …] ×{v.vector_dim}
        </span>
      );
    }
    const b = fmtBytes(v.bytes);
    return <span style={{ color: "var(--index)" }}>{b.value} {b.unit}</span>;
  }
  if (typeof v === "number") return <>{Number.isInteger(v) ? v.toLocaleString() : v.toFixed(3)}</>;
  return <>{String(v)}</>;
}
