"use client";

/** The left rail.
 *
 *  A flat list of every table was fine at three tables and useless at forty. This
 *  is the conventional shape instead — filter at the top, then pinned, then what
 *  you were just looking at, then everything — because that is the shape people
 *  already know how to read, and because the useful question in a console is
 *  rarely "what is the alphabetically first table" but "the one I had open".
 */

import { useMemo } from "react";
import Icon from "@/app/components/Icon";
import { fmtBytes } from "@/app/lib/api";
import type { TableRef } from "@/app/lib/catalog";

export default function TableRail({
  tables,
  picked,
  query,
  onQuery,
  onPick,
  pins,
  onTogglePin,
  recents,
  listBytes,
}: {
  tables: TableRef[] | null;
  picked: string | null;
  query: string;
  onQuery: (q: string) => void;
  onPick: (name: string) => void;
  pins: string[];
  onTogglePin: (name: string) => void;
  recents: string[];
  listBytes: number | null;
}) {
  const matches = useMemo(() => {
    if (!tables) return [];
    const q = query.trim().toLowerCase();
    return q ? tables.filter((t) => t.name.toLowerCase().includes(q)) : tables;
  }, [tables, query]);

  const by = useMemo(
    () => new Map((tables ?? []).map((t) => [t.name, t])),
    [tables],
  );

  // Pins and recents are names remembered in the browser; a table can have been
  // dropped since, so both lists are resolved against what actually exists now.
  const pinned = query ? [] : pins.map((n) => by.get(n)).filter(Boolean) as TableRef[];
  const recent = query
    ? []
    : (recents.map((n) => by.get(n)).filter(Boolean) as TableRef[])
        .filter((t) => !pins.includes(t.name))
        .slice(0, 4);

  const searching = query.trim().length > 0;

  return (
    <nav className="w-full lg:w-[262px] shrink-0">
      <div className="relative mb-4">
        <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--dim)] pointer-events-none">
          <Icon name="search" size={14} />
        </span>
        <input
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          placeholder="Filter tables"
          aria-label="Filter tables"
          className="inp !pl-8 !pr-8 !py-2"
        />
        {searching && (
          <button
            onClick={() => onQuery("")}
            aria-label="Clear filter"
            className="absolute right-2 top-1/2 -translate-y-1/2 text-[var(--dim)]
                       hover:text-[var(--bright)] transition-colors"
          >
            <Icon name="close" size={13} />
          </button>
        )}
      </div>

      {tables === null ? (
        <div className="eyebrow px-1">loading</div>
      ) : (
        <div className="space-y-5">
          <Section title="Pinned" icon="starFilled" rows={pinned} {...{ picked, onPick, pins, onTogglePin }} />
          <Section title="Recent" icon="clock" rows={recent} {...{ picked, onPick, pins, onTogglePin }} />
          <Section
            title={searching ? `${matches.length} match${matches.length === 1 ? "" : "es"}` : "All tables"}
            icon="table"
            count={searching ? undefined : matches.length}
            rows={matches}
            empty={searching ? `Nothing matching “${query}”.` : "No tables here."}
            {...{ picked, onPick, pins, onTogglePin }}
          />
        </div>
      )}

      {listBytes !== null && (
        <p className="text-[11px] text-[var(--haze)] leading-relaxed pt-5">
          Listing every table cost{" "}
          <span className="mono" style={{ color: "var(--index)" }}>
            {fmtBytes(listBytes).value} {fmtBytes(listBytes).unit}
          </span>
          . It reads manifests, never data.
        </p>
      )}
    </nav>
  );
}

function Section({
  title, icon, rows, count, empty, picked, onPick, pins, onTogglePin,
}: {
  title: string;
  icon: "starFilled" | "clock" | "table";
  rows: TableRef[];
  count?: number;
  empty?: string;
  picked: string | null;
  onPick: (n: string) => void;
  pins: string[];
  onTogglePin: (n: string) => void;
}) {
  if (!rows.length && !empty) return null;
  return (
    <section>
      <div className="eyebrow flex items-center gap-2 mb-2 px-1">
        <Icon name={icon} size={12} />
        {title}
        {count !== undefined && <span className="text-[var(--dim)]">{count}</span>}
      </div>
      {rows.length === 0 ? (
        <p className="text-[12px] text-[var(--haze)] px-1 py-2">{empty}</p>
      ) : (
        <div className="space-y-1">
          {rows.map((t) => (
            <Row
              key={t.name}
              t={t}
              on={t.name === picked}
              pinned={pins.includes(t.name)}
              onPick={() => onPick(t.name)}
              onPin={() => onTogglePin(t.name)}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function Row({ t, on, pinned, onPick, onPin }: {
  t: TableRef; on: boolean; pinned: boolean; onPick: () => void; onPin: () => void;
}) {
  return (
    <div
      className="group relative rounded-sm border transition-colors"
      style={on
        ? { borderColor: "var(--video)", background: "rgb(var(--video-rgb) / 0.09)" }
        : { borderColor: "var(--rule)" }}
    >
      <button onClick={onPick} className="w-full text-left pl-3 pr-9 py-2.5">
        <span className="flex items-center gap-2 mb-1">
          <span style={{ color: on ? "var(--video)" : "var(--haze)" }}>
            <Icon name="table" size={13} />
          </span>
          <span className="mono text-[13px] truncate"
                style={{ color: on ? "var(--video)" : "var(--bright)" }}>
            {t.name}
          </span>
        </span>
        <span className="mono block text-[10px] text-[var(--haze)] pl-[21px]">
          {t.rows.toLocaleString()} rows · {t.columns} cols · v{t.version}
        </span>
        {t.blob_columns.length > 0 && (
          <span className="mono flex items-center gap-1.5 text-[10px] mt-1 pl-[21px]"
                style={{ color: "var(--video)" }}>
            <Icon name="fragments" size={10} />
            {t.blob_columns.length} blob column{t.blob_columns.length === 1 ? "" : "s"}
          </span>
        )}
      </button>

      {/* Pinning is a second action on a row whose primary action is "open me", so
          it is its own button rather than a click target inside the first one. */}
      <button
        onClick={onPin}
        aria-label={pinned ? `Unpin ${t.name}` : `Pin ${t.name}`}
        data-tip={pinned ? "Unpin" : "Pin"}
        data-tip-side="left"
        className={`absolute right-1.5 top-1.5 w-6 h-6 grid place-items-center rounded-sm
                    transition-opacity hover:text-[var(--index)]
                    ${pinned ? "opacity-100" : "opacity-0 group-hover:opacity-100 focus-visible:opacity-100"}`}
        style={{ color: pinned ? "var(--index)" : "var(--haze)" }}
      >
        <Icon name={pinned ? "starFilled" : "star"} size={14} />
      </button>
    </div>
  );
}
