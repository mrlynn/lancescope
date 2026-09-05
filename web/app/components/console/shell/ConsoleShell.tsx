"use client";

/** The workspace: a fixed toolbar over three panes that do not scroll each other.
 *
 *  Everything under `/console` used to be four sibling routes, each building its own
 *  `<main>` and its own `<AppBar>` from nothing. Moving between them threw the whole
 *  console away and rebuilt it — which is why activating a connection in settings and
 *  returning showed an empty database for a moment before the list came back.
 *
 *  Next keeps a layout mounted across navigations between the routes it wraps, so
 *  this is the fix in one line of architecture. It lives in a route group rather
 *  than at `app/console/layout.tsx`, because that would also wrap `/console/settings`
 *  and `/console/bundle`, and neither of those wants workspace chrome: settings has
 *  to work when nothing is connected, and the bundle viewer is reading *somebody
 *  else's* database with the root redacted, so a toolbar naming your connection and
 *  a rail listing your tables would be a lie about what is on screen.
 */

import Link from "next/link";
import { type ReactNode, useCallback, useEffect, useState } from "react";

import Icon from "@/app/components/Icon";
import Mark from "@/app/components/Mark";
import ThemeToggle from "@/app/components/ThemeToggle";
import DbSwitcher from "@/app/components/nav/DbSwitcher";
import Inspector from "@/app/components/console/shell/Inspector";
import TableRail from "@/app/components/console/TableRail";
import { listTables } from "@/app/lib/catalog";
import { usePins, useRecents } from "@/app/lib/recents";
import { activateConnection } from "@/app/lib/settings";
import { notePush, setParams, useValue } from "@/app/lib/url-state";
import { useHistoryDepth } from "@/app/lib/url-state";
import { clearConnection, clearTable, recordCost, set, useWorkspace } from "@/app/lib/workspace";

/** Which pane a narrow window is showing. Three columns need a width that a phone
 *  does not have, and hiding two of them is more honest than shrinking all three
 *  past the point of use. */
type Narrow = "centre" | "rail" | "inspector";

export default function ConsoleShell({ children }: { children: ReactNode }) {
  const w = useWorkspace();
  const [narrow, setNarrow] = useState<Narrow>("centre");
  const history = useHistoryDepth();

  // The shell owns the selection, because the rail that changes it and the
  // inspector that describes it both live here and the page in the middle is only
  // one of the things that reads it.
  const table = useValue("table");
  const [railQuery, setRailQuery] = useState("");
  const root = w.list?.root ?? w.settings?.root.root ?? null;
  const { recents, touch } = useRecents(root);
  const { pins, toggle: togglePin } = usePins(root);

  const pick = useCallback((name: string) => {
    clearTable();
    touch(name);
    // A table is somewhere you were. Back should return you to the last one.
    setParams({ table: name }, "push");
    notePush();
  }, [touch]);

  const onSwitch = useCallback((id: string) => {
    clearConnection();
    setRailQuery("");
    // A different database is a different place, and the table you were on does not
    // exist in it — carrying the name across would land on a 404 named like a table.
    setParams({ table: null }, "push");
    notePush();
    activateConnection(id)
      .then(() => listTables())
      .then((d) => {
        set({ list: d });
        recordCost("list tables", d.read_bytes, d.read_iops);
      })
      .catch((e) => set({ listError: e instanceof Error ? e.message : "unreachable" }));
  }, []);

  // A finding names the panel its evidence is on. Clicking one goes there, which is
  // the whole reason the index is worth having apart from the cards.
  const onGoToPanel = useCallback((panel: string) => {
    setParams({ tab: panel === "rows" ? "query" : panel }, "push");
    notePush();
  }, []);

  // Back and forward are the browser's. Binding ⌘[ and ⌘] would be the app fighting
  // the platform on both surfaces — the webview honours them already, and so does a
  // browser tab. These are buttons for the mouse, not a second set of shortcuts.
  const back = useCallback(() => window.history.back(), []);
  const forward = useCallback(() => window.history.forward(), []);

  // The window is the workspace's, for as long as the workspace is on screen. Taken
  // off again on unmount, because `/console/settings` and `/console/bundle` are
  // documents and a document that cannot scroll is a document with a bottom half
  // nobody can read.
  useEffect(() => {
    document.documentElement.classList.add("app-locked");
    return () => document.documentElement.classList.remove("app-locked");
  }, []);

  // Coming back to a wide window with a pane still selected would leave the centre
  // hidden on a layout that has room for all three.
  useEffect(() => {
    const wide = window.matchMedia("(min-width: 1024px)");
    const settle = () => wide.matches && setNarrow("centre");
    wide.addEventListener("change", settle);
    return () => wide.removeEventListener("change", settle);
  }, []);

  return (
    <div className="console-shell">
      <header className="console-toolbar">
        <Link href="/" title="LanceScope — an independent tool, not affiliated with LanceDB"
              className="shrink-0 flex items-center gap-2 opacity-90 hover:opacity-100
                         transition-opacity">
          <Mark size={18} className="text-[var(--haze)]" />
          <span className="text-[14px] font-extrabold tracking-tight text-[var(--bright)]
                           hidden sm:inline">
            LanceScope
          </span>
        </Link>

        <div className="flex items-center gap-1" data-optional>
          <button className="iconbtn !w-7 !h-7" onClick={back} disabled={!history.back}
                  data-tip={history.back ? "Back" : "Nothing further back in this session"}
                  aria-label="Back">
            <Icon name="chevronLeft" size={14} />
          </button>
          <button className="iconbtn !w-7 !h-7" onClick={forward} disabled={!history.forward}
                  data-tip={history.forward ? "Forward" : "Nothing further forward"}
                  aria-label="Forward">
            <Icon name="chevronRight" size={14} />
          </button>
        </div>

        <div className="min-w-0 shrink">
          <DbSwitcher
            settings={w.settings}
            root={w.list?.root ?? null}
            tableCount={w.list?.tables.length}
            onSwitch={onSwitch}
          />
        </div>

        <div className="ml-auto flex items-center gap-1.5">
          {/* The two panes a narrow window has to choose between. Hidden above the
              breakpoint, where all three are on screen and a toggle would be a
              control that does nothing. */}
          <div className="lg:hidden flex items-center gap-1.5">
            <button className="iconbtn !w-7 !h-7" aria-label="Tables"
                    data-on={narrow === "rail"} data-tip="Tables"
                    onClick={() => setNarrow((n) => (n === "rail" ? "centre" : "rail"))}>
              <Icon name="table" size={14} />
            </button>
            <button className="iconbtn !w-7 !h-7" aria-label="Inspector"
                    data-on={narrow === "inspector"} data-tip="Inspector"
                    onClick={() => setNarrow((n) => (n === "inspector" ? "centre" : "inspector"))}>
              <Icon name="info" size={14} />
            </button>
          </div>
          <button className="iconbtn !w-7 !h-7" onClick={() => setParams({}, "replace")}
                  data-tip="Refresh" aria-label="Refresh"
                  onDoubleClick={() => window.location.reload()}>
            <Icon name="refresh" size={14} />
          </button>
          <Link href="/console/new" className="iconbtn !w-7 !h-7"
                data-tip="Build one from files" data-optional aria-label="Build a database from files">
            <Icon name="plus" size={14} />
          </Link>
          <Link href="/console/bundle" className="iconbtn !w-7 !h-7"
                data-tip="Open a bundle somebody sent you" data-optional aria-label="Open a bundle">
            <Icon name="external" size={14} />
          </Link>
          <Link href="/docs/index" className="iconbtn !w-7 !h-7" data-tip="Guide" data-optional
                data-tip-side="left" aria-label="Guide">
            <Icon name="info" size={14} />
          </Link>
          <span data-optional><ThemeToggle /></span>
          <Link href="/console/settings" className="iconbtn !w-7 !h-7" data-tip="Settings"
                data-tip-side="left" aria-label="Settings">
            <Icon name="settings" size={14} />
          </Link>
        </div>
      </header>

      <div className="console-body" data-pane={narrow}>
        <nav className="console-pane console-rail" aria-label="Tables">
          <TableRail
            tables={w.list?.tables ?? null}
            picked={table}
            query={railQuery}
            onQuery={setRailQuery}
            onPick={pick}
            pins={pins}
            onTogglePin={togglePin}
            recents={recents}
            listBytes={w.list?.read_bytes ?? null}
          />
        </nav>
        <section className="console-pane console-centre">{children}</section>
        <Inspector w={w} table={table} onGoToPanel={onGoToPanel} />
      </div>
    </div>
  );
}
