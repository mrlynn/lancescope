"use client";

import { useState } from "react";

import Icon from "@/app/components/Icon";
import { fmtBytes } from "@/app/lib/api";

/** Copy-to-clipboard, for the handful of values on this console that exist to be
 *  pasted somewhere else — a table path, a run config, a reproduction.
 *
 *  There were three of these, written separately and behaving almost the same. The
 *  `title` prop is what let the third one fold in: where the clipboard is refused,
 *  the value still has to be reachable, and on the table header that was a tooltip
 *  carrying the path. Failing silently is deliberate — a permission dialog the user
 *  already declined is not worth an error state, and the value is on screen. */
export function Copy({ value, what, title, size = 14, className = "" }: {
  value: string; what: string; title?: string; size?: number; className?: string;
}) {
  const [done, setDone] = useState(false);
  return (
    <button
      className={`iconbtn shrink-0 ${className}`}
      title={title}
      data-tip={done ? "Copied" : `Copy ${what}`}
      aria-label={`Copy ${what}`}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setDone(true);
          setTimeout(() => setDone(false), 1400);
        } catch {
          // No clipboard permission; the value is on screen and selectable anyway.
        }
      }}
    >
      <Icon name={done ? "check" : "external"} size={size} />
    </button>
  );
}

/** What the request you just made cost. The console is a tool for reading byte
 *  costs, so it states its own in the same coral/amber language as the demo. */
export function Cost({ bytes, iops, label = "this read" }: {
  bytes: number; iops?: number; label?: string;
}) {
  const b = fmtBytes(bytes);
  return (
    <span className="mono text-[10px] text-[var(--haze)] whitespace-nowrap">
      {label}{" "}
      <span style={{ color: bytes === 0 ? "var(--haze)" : "var(--index)" }}>
        {bytes === 0 ? "nothing" : `${b.value} ${b.unit}`}
      </span>
      {iops !== undefined && iops > 0 && (
        <span className="text-[var(--dim)]"> · {iops} iops</span>
      )}
    </span>
  );
}

export function Bytes({ n, tone }: { n: number; tone?: "index" | "video" }) {
  const b = fmtBytes(n);
  return (
    <span
      className="mono"
      style={{ color: tone ? `var(--${tone})` : "var(--body)" }}
    >
      {b.value}
      <span className="text-[0.82em] ml-0.5 text-[var(--haze)]">{b.unit}</span>
    </span>
  );
}

export function Eyebrow({ children }: { children: React.ReactNode }) {
  return <div className="eyebrow mb-3">{children}</div>;
}

export function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[13px] text-[var(--haze)] py-10 text-center leading-relaxed">
      {children}
    </div>
  );
}

/** A short, quiet explanation of something the numbers above would otherwise
 *  misrepresent. Used where Lance's own accounting needs a caveat. */
export function Caveat({ children }: { children: React.ReactNode }) {
  return (
    <p
      className="text-[12px] leading-relaxed mt-4 p-3.5 rounded-sm"
      style={{
        color: "var(--body)",
        background: "rgb(var(--index-rgb) / 0.07)",
        border: "1px solid rgb(var(--index-rgb) / 0.28)",
      }}
    >
      {children}
    </p>
  );
}

export function Th({ children, right = false }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th
      className={`eyebrow font-normal pb-2 px-3 ${right ? "text-right" : "text-left"}`}
      style={{ borderBottom: "1px solid var(--rule)" }}
    >
      {children}
    </th>
  );
}

export function Td({ children, right = false, dim = false, className = "" }: {
  children: React.ReactNode; right?: boolean; dim?: boolean; className?: string;
}) {
  return (
    <td
      className={`mono text-[12px] py-2 px-3 align-top ${right ? "text-right" : ""} ${className}`}
      style={{ color: dim ? "var(--haze)" : "var(--body)" }}
    >
      {children}
    </td>
  );
}

export function fmtWhen(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}
