"use client";

/** Go to anything, by typing part of its name.
 *
 *  Every source here already existed and was reachable only by knowing where it was
 *  kept: tables in the rail, saved queries at the foot of the query workspace,
 *  connections behind the switcher, findings in the inspector, settings two clicks
 *  away. A palette does not add them; it stops the answer depending on which panel
 *  you happened to open.
 *
 *  Ranking is exact prefix, then word-boundary prefix, then substring, then the
 *  group's own order. Dumb and explainable on purpose. A fuzzy matcher that surfaces
 *  `fragments` for `qy` is the kind of magic this console would have to justify, and
 *  there is no justification — the names are short and the reader knows them.
 *
 *  Rows show what they cost where the cost is already known. A palette that hid the
 *  price while every other surface shows it is the one place the argument would leak.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import Icon, { type IconName } from "@/app/components/Icon";
import { Bytes } from "@/app/components/console/atoms";
import type { Finding } from "@/app/lib/catalog";
import { BINDINGS } from "@/app/lib/keys";
import { describeSpec, useQueryHistory, useSavedQueries } from "@/app/lib/queries";
import { usePins, useRecents } from "@/app/lib/recents";
import type { Workspace } from "@/app/lib/workspace";

type Item = {
  id: string;
  group: string;
  label: string;
  hint?: string;
  icon: IconName;
  bytes?: number;
  /** Disabled with a reason rather than hidden. "Explain, do not show empty as
   *  success" applies to a command as much as to a connection. */
  why?: string;
  run: () => void;
};

/** Exact prefix beats a prefix at a word boundary beats a substring. Anything else
 *  does not match at all. */
function score(label: string, q: string): number {
  if (!q) return 0;
  const l = label.toLowerCase();
  if (l.startsWith(q)) return 0;
  if (new RegExp(`\\b${q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`).test(l)) return 1;
  if (l.includes(q)) return 2;
  return -1;
}

export default function Palette({ w, open, onClose, root, onGo, onScreen, onSwitch, onGoToPanel }: {
  w: Workspace;
  open: boolean;
  onClose: () => void;
  root: string | null;
  /** A route, for the things that are pages. */
  onGo: (href: string) => void;
  onScreen: (tab: string, table?: string) => void;
  onSwitch: (id: string) => void;
  onGoToPanel: (panel: Finding["panel"]) => void;
}) {
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const { recents } = useRecents(root);
  const { pins } = usePins(root);
  const saved = useSavedQueries(root);
  const history = useQueryHistory(root);

  // Focus only. There is no state to reset here because the shell mounts this
  // fresh each time it opens — a component that has to remember to forget is a
  // component that will one day forget to.
  useEffect(() => {
    requestAnimationFrame(() => input.current?.focus());
  }, []);

  const items = useMemo<Item[]>(() => {
    const out: Item[] = [];
    const tables = w.list?.tables ?? [];
    const pinned = new Set(pins);
    const recent = new Set(recents);

    for (const t of tables) {
      out.push({
        id: `table:${t.name}`,
        group: pinned.has(t.name) ? "Pinned" : recent.has(t.name) ? "Recent" : "Tables",
        label: t.name,
        hint: `${t.rows.toLocaleString()} rows · ${t.columns} cols · v${t.version}`,
        icon: "table",
        // What opening it reads. Already in hand from the listing.
        bytes: t.manifest_bytes,
        run: () => onScreen("schema", t.name),
      });
    }

    for (const [tab, label] of [["schema", "Table"], ["query", "Query"], ["compare", "Compare"],
                                ["training", "Training"], ["data", "Data"]] as const) {
      out.push({
        id: `screen:${tab}`, group: "Screens", label, icon: "schema",
        run: () => onScreen(tab),
      });
    }

    for (const f of w.findings?.findings ?? []) {
      out.push({
        id: `finding:${f.id}`,
        group: "Findings",
        label: f.title,
        hint: `evidence on ${f.panel}`,
        icon: f.severity === "warn" ? "warning" : "info",
        run: () => onGoToPanel(f.panel),
      });
    }

    for (const c of w.settings?.connections ?? []) {
      out.push({
        id: `conn:${c.id}`,
        group: "Connections",
        label: c.label,
        hint: c.uri,
        icon: "database",
        // `LANCE_ROOT` wins over saved connections, and the settings page greys the
        // list out rather than letting somebody pick something with no effect.
        why: w.settings?.env_locked ? "LANCE_ROOT is set — it wins over saved connections" : undefined,
        run: () => onSwitch(c.id),
      });
    }

    for (const s of saved.saved) {
      out.push({
        id: `saved:${s.id}`, group: "Saved queries", label: s.name ?? describeSpec(s.spec),
        hint: s.table, icon: "search", bytes: s.last?.read_bytes,
        run: () => onScreen("query", s.table),
      });
    }
    for (const h of history.history.slice(0, 6)) {
      out.push({
        id: `hist:${h.id}`, group: "Recent queries", label: describeSpec(h.spec),
        hint: h.table, icon: "clock", bytes: h.last?.read_bytes,
        run: () => onScreen("query", h.table),
      });
    }

    for (const [href, label] of [["/console/new", "Build a database from files"],
                                 ["/console/bundle", "Open a bundle"],
                                 ["/console/settings", "Settings"],
                                 ["/docs/index", "Guide"]] as const) {
      out.push({ id: `go:${href}`, group: "Go to", label, icon: "arrowRight", run: () => onGo(href) });
    }

    return out;
  }, [w, pins, recents, saved.saved, history.history, onScreen, onGo, onSwitch, onGoToPanel]);

  const shown = useMemo(() => {
    const query = q.trim().toLowerCase();
    if (!query) return items.slice(0, 40);
    return items
      .map((i) => ({ i, s: score(i.label, query) }))
      .filter((x) => x.s >= 0)
      .sort((a, b) => a.s - b.s)
      .slice(0, 40)
      .map((x) => x.i);
  }, [items, q]);

  const choose = useCallback((i: Item) => {
    if (i.why) return;
    i.run();
    onClose();
  }, [onClose]);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, shown.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); if (shown[active]) choose(shown[active]); }
    else if (e.key === "Escape") { e.preventDefault(); onClose(); }
  };

  /** Each group once, in the order its best match appears.
   *
   *  Walking the list and starting a new section whenever the name changes puts the
   *  same heading on screen twice the moment two tables are separated by a recent
   *  one — which is the ordinary case, not the odd one. Keyed by name instead, so a
   *  group is a group however its members are scattered by relevance. */
  const grouped = useMemo(() => {
    const byName = new Map<string, Item[]>();
    for (const i of shown) {
      const at = byName.get(i.group);
      if (at) at.push(i);
      else byName.set(i.group, [i]);
    }
    return [...byName].map(([group, items]) => ({ group, items }));
  }, [shown]);

  if (!open) return null;

  let n = -1;
  return (
    <>
      <div className="fixed inset-0 z-[80]" style={{ background: "rgb(0 0 0 / 0.3)" }}
           onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Go to anything"
        className="fixed z-[81] left-1/2 -translate-x-1/2 top-[12vh] w-[min(620px,92vw)]
                   panel shadow-2xl flex flex-col max-h-[70vh]"
      >
        <input
          ref={input}
          value={q}
          onChange={(e) => { setQ(e.target.value); setActive(0); }}
          onKeyDown={onKey}
          placeholder="Go to a table, a screen, a finding, a database…"
          aria-label="Search"
          className="qin !border-0 !rounded-none w-full px-4 py-3 text-[14px]"
          style={{ borderBottom: "1px solid var(--rule)" }}
        />
        <div className="overflow-y-auto py-1">
          {shown.length === 0 && (
            <p className="text-[12px] text-[var(--haze)] px-4 py-6 text-center">
              Nothing matching “{q}”.
            </p>
          )}
          {grouped.map((g) => (
            <div key={g.group}>
              <div className="eyebrow px-4 pt-2.5 pb-1">{g.group}</div>
              {g.items.map((i) => {
                n += 1;
                const at = n;
                return (
                <button
                  key={i.id}
                  onMouseEnter={() => setActive(at)}
                  onClick={() => choose(i)}
                  data-on={at === active}
                  disabled={!!i.why}
                  title={i.why}
                  className="w-full text-left px-4 py-1.5 flex items-baseline gap-2.5
                             disabled:opacity-45"
                  style={at === active ? { background: "rgb(var(--video-rgb) / 0.09)" } : undefined}
                >
                  <span className="shrink-0 self-center text-[var(--haze)]">
                    <Icon name={i.icon} size={13} />
                  </span>
                  <span className="mono text-[12px] text-[var(--bright)] truncate">{i.label}</span>
                  {i.hint && (
                    <span className="text-[11px] text-[var(--haze)] truncate">{i.hint}</span>
                  )}
                  {i.bytes !== undefined && (
                    <span className="ml-auto shrink-0 text-[10px]">
                      <Bytes n={i.bytes} />
                    </span>
                  )}
                </button>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

/** The bindings, as a list, generated from the same table the dispatcher reads.
 *  A cheat sheet maintained by hand is a cheat sheet that lies. */
export function Shortcuts({ open, onClose }: { open: boolean; onClose: () => void }) {
  if (!open) return null;
  return (
    <>
      <div className="fixed inset-0 z-[80]" style={{ background: "rgb(0 0 0 / 0.3)" }}
           onClick={onClose} />
      <div role="dialog" aria-modal="true" aria-label="Keyboard shortcuts"
           className="fixed z-[81] left-1/2 -translate-x-1/2 top-[16vh] w-[min(420px,92vw)]
                      panel shadow-2xl p-5">
        <div className="eyebrow mb-3">keyboard</div>
        <table className="w-full">
          <tbody>
            {BINDINGS.map((b) => (
              <tr key={b.action}>
                <td className="mono text-[11px] text-[var(--bright)] py-1 pr-4 whitespace-nowrap">
                  {b.keys}
                </td>
                <td className="text-[12px] text-[var(--haze)] py-1">{b.says}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
