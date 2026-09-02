"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import Icon, { type IconName } from "@/app/components/Icon";
import Mark from "@/app/components/Mark";
import AppBar from "@/app/components/nav/AppBar";
import DbSwitcher from "@/app/components/nav/DbSwitcher";
import SampleDatasets from "@/app/components/samples/SampleDatasets";
import { Cost, Empty } from "@/app/components/console/atoms";
import TableRail from "@/app/components/console/TableRail";
import {
  FragmentsTab, IndicesTab, RowsTab, SchemaTab, VersionsTab,
} from "@/app/components/console/tabs";
import {
  ApiError,
  type Findings, type Fragments, type Indices, type Rows, type TableDetail,
  type TableList, type Versions,
  getFindings, getFragments, getIndices, getRows, getTable, getVersions, listTables,
} from "@/app/lib/catalog";
import {
  InsightsTab, PanelFindings, PartialAnalysis,
} from "@/app/components/console/Findings";
import { CompareTab } from "@/app/components/console/CompareTab";
import { QueryTab } from "@/app/components/console/QueryTab";
import { usePins, useRecents } from "@/app/lib/recents";
import {
  type Capabilities, type SettingsState, activateConnection, getCapabilities,
  getSettings,
} from "@/app/lib/settings";

// Each tab names what it reads, and carries the glyph for it — the row is
// scannable as shapes before any of the words are read.
const TABS: { id: string; icon: IconName }[] = [
  { id: "schema", icon: "schema" },
  { id: "versions", icon: "history" },
  { id: "indices", icon: "index" },
  { id: "fragments", icon: "fragments" },
  { id: "rows", icon: "rows" },
  { id: "query", icon: "search" },
  { id: "compare", icon: "history" },
  { id: "insights", icon: "spark" },
];
type Tab = "schema" | "versions" | "indices" | "fragments" | "rows" | "query"
  | "compare" | "insights";

const PAGE = 25;

export default function Console() {
  const [list, setList] = useState<TableList | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("schema");
  const [railQuery, setRailQuery] = useState("");

  const [detail, setDetail] = useState<TableDetail | null>(null);
  const [versions, setVersions] = useState<Versions | null>(null);
  const [indices, setIndices] = useState<Indices | null>(null);
  const [fragments, setFragments] = useState<Fragments | null>(null);
  const [rows, setRows] = useState<Rows | null>(null);
  // Fetched once per table rather than per tab: the same list is rendered inline
  // under four panels and collected in Insights, and it is one cheap metadata read.
  const [findings, setFindings] = useState<Findings | null>(null);

  const [offset, setOffset] = useState(0);
  // How wide a page read is. Kept here rather than in the panel because every
  // paging control has to agree with it, and because it survives a tab switch.
  const [limit, setLimit] = useState(PAGE);
  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState<string[]>([]);
  const [rowsError, setRowsError] = useState<string | null>(null);
  const [cost, setCost] = useState<{ bytes: number; iops: number } | null>(null);
  const [settings, setSettings] = useState<SettingsState | null>(null);
  // What the language layer can do right now, so the console offers it only when it
  // is actually there — and stays exactly as useful when it is not.
  const [ai, setAi] = useState<Capabilities | null>(null);

  // Pins and recents are scoped to the database, so they follow a connection
  // switch rather than showing the last database's history against this one.
  const root = list?.root ?? settings?.root.root ?? null;
  const { recents, touch } = useRecents(root);
  const { pins, toggle: togglePin } = usePins(root);

  // Everything downstream of the selected table is cleared here rather than in an
  // effect keyed on `picked`: an effect would re-render twice on every click, and
  // the reset is a consequence of the click, not of the state having changed.
  const selectTable = useCallback((name: string | null) => {
    setPicked(name);
    setDetail(null); setVersions(null); setIndices(null); setFragments(null);
    setFindings(null);
    setRows(null); setOffset(0); setFilter(""); setExpanded([]); setRowsError(null);
  }, []);

  const loadTables = useCallback((want?: string | null, wantTab?: string | null) => {
    listTables()
      .then((d) => {
        setList(d);
        const has = (n: string | null | undefined) => !!n && d.tables.some((t) => t.name === n);
        const picked = has(want) ? want! : null;
        setPicked((p) => picked ?? (has(p) ? p : d.tables[0]?.name ?? null));
        // `?tab=` opens the console on the question rather than the schema — "here
        // are your 23 columns" is a strange first answer to "find my photos". Set
        // here, alongside the table it belongs to, rather than in an effect of its
        // own: a synchronous setState in an effect is a cascading render.
        if (picked && wantTab && TABS.some((x) => x.id === wantTab)) {
          setTab(wantTab as Tab);
        }
      })
      .catch((e) => setListError(e instanceof Error ? e.message : "unreachable"));
  }, []);

  /** `?table=` deep-links straight to one table — what the recent-table chips on the
   *  home screen point at. Read from `location` rather than `useSearchParams` so this
   *  page keeps prerendering without a Suspense boundary around the whole console. */
  const wanted = () => {
    if (typeof window === "undefined") return null;
    return new URLSearchParams(window.location.search).get("table");
  };

  const wantedTab = () => {
    if (typeof window === "undefined") return null;
    return new URLSearchParams(window.location.search).get("tab");
  };

  useEffect(() => {
    loadTables(wanted(), wantedTab());
    getSettings().then(setSettings).catch(() => setSettings(null));
    getCapabilities().then(setAi).catch(() => setAi(null));
  }, [loadTables]);

  // Switching connection repoints the catalog server-side, so everything below the
  // rail is about a different database now. Clear it rather than leaving a schema
  // from the old one on screen while the new list arrives.
  const switchTo = useCallback(async (id: string) => {
    try {
      setSettings(await activateConnection(id));
      selectTable(null);
      setList(null);
      setCost(null);
      setRailQuery("");
      loadTables();
    } catch (e) {
      setListError(e instanceof Error ? e.message : "could not switch connection");
    }
  }, [loadTables, selectTable]);

  const loadRows = useCallback(
    async (name: string, off: number, f: string, exp: string[], lim = PAGE) => {
      try {
        const d = await getRows(name, {
          offset: off, limit: lim, filter: f || null, expand: exp,
        });
        setRows(d);
        setRowsError(null);
        setCost({ bytes: d.read_bytes, iops: d.read_iops });
      } catch (e) {
        // A filter the user typed is theirs to fix; keep the page they were on.
        setRowsError(e instanceof ApiError ? e.message : "request failed");
      }
    },
    [],
  );

  useEffect(() => {
    if (!picked) return;
    let alive = true;
    getFindings(picked)
      .then((d) => { if (alive) setFindings(d); })
      .catch(() => { if (alive) setFindings(null); });
    return () => { alive = false; };
  }, [picked]);

  useEffect(() => {
    if (!picked) return;
    let alive = true;
    const run = async () => {
      try {
        if (tab === "schema" && !detail) {
          const d = await getTable(picked);
          if (!alive) return;
          setDetail(d); setCost({ bytes: d.read_bytes, iops: d.read_iops });
        } else if (tab === "versions" && !versions) {
          const d = await getVersions(picked);
          if (!alive) return;
          setVersions(d); setCost({ bytes: d.read_bytes, iops: d.read_iops });
        } else if (tab === "indices" && !indices) {
          const d = await getIndices(picked);
          if (!alive) return;
          setIndices(d); setCost({ bytes: d.read_bytes, iops: d.read_iops });
        } else if (tab === "fragments" && !fragments) {
          const d = await getFragments(picked);
          if (!alive) return;
          setFragments(d); setCost({ bytes: d.read_bytes, iops: d.read_iops });
        } else if (tab === "rows" && !rows) {
          await loadRows(picked, 0, "", []);
        }
      } catch (e) {
        if (alive) setListError(e instanceof Error ? e.message : "request failed");
      }
    };
    run();
    return () => { alive = false; };
  }, [picked, tab, detail, versions, indices, fragments, rows, loadRows]);

  const current = list?.tables.find((t) => t.name === picked) ?? null;

  return (
    <main className="relative z-10 min-h-screen px-[var(--stage-pad)] pt-7 pb-16">
      <AppBar crumbs={[{ label: "Console" }]}>
        {cost && <Cost bytes={cost.bytes} iops={cost.iops} />}
        <Link href="/console/new" className="iconbtn" data-tip="Build one from files"
              aria-label="Build a database from files">
          <Icon name="plus" size={16} />
        </Link>
        <Link href="/demo" className="iconbtn" data-tip="Ctrl-F for Video" aria-label="Open the demo">
          <Icon name="play" size={16} />
        </Link>
      </AppBar>

      <div className="flex items-center gap-3 mb-7 flex-wrap">
        <DbSwitcher
          settings={settings}
          root={list?.root ?? null}
          tableCount={list?.tables.length}
          onSwitch={switchTo}
        />
        {current && (
          <>
            <span className="text-[var(--dim)]" aria-hidden><Icon name="chevronRight" size={13} /></span>
            <span className="mono text-[13px] text-[var(--bright)]">{current.name}</span>
            <CopyPath uri={current.uri} />
          </>
        )}
      </div>

      {listError && (
        <div className="mono flex items-center gap-2.5 text-[12px] px-3.5 py-3 rounded-sm mb-6"
             style={{ background: "rgb(var(--video-rgb) / 0.12)",
                      border: "1px solid rgb(var(--video-rgb) / 0.4)", color: "var(--video)" }}>
          <Icon name="warning" size={15} />
          {listError}
        </div>
      )}

      {list && list.tables.length === 0 ? (
        <div className="mt-24 text-center">
          <Mark size={34} mono className="mx-auto mb-5 text-[var(--rule)]" />
          {/* Two different facts that used to render as the same sentence: a
              database with nothing in it, and a connection this tool cannot read.
              One is about the data; the other is about us. */}
          {list.capabilities && !list.capabilities.discover.available ? (
            <div className="max-w-xl mx-auto">
              <p className="text-[15px] text-[var(--bright)] leading-relaxed">
                Connected, and this cannot be browsed.
              </p>
              <p className="text-[14px] text-[var(--haze)] leading-relaxed mt-3">
                {list.capabilities.discover.reason}
              </p>
              <p className="mono text-[12px] text-[var(--haze)] mt-4 break-all">
                {list.root}
              </p>
              <p className="text-[13px] text-[var(--haze)] leading-relaxed mt-4">
                <Link href="/console/settings" className="underline"
                      style={{ color: "var(--video)" }}>Switch to a local directory</Link>{" "}
                to browse a database here.
              </p>
            </div>
          ) : (
            <div className="max-w-3xl mx-auto text-left">
              <p className="text-[15px] text-[var(--haze)] leading-relaxed text-center">
                No Lance tables under{" "}
                <span className="mono text-[var(--bright)]">{list.root || "any configured path"}</span>.
              </p>

              {/* Three ways forward, and the cheapest one first. Someone who has
                  nothing to look at cannot evaluate a console for reading data, and
                  telling them to go and make some is the slowest of the three. */}
              <div className="mt-9">
                <div className="eyebrow mb-3">Open something public</div>
                <SampleDatasets compact onOpened={() => window.location.reload()} />
              </div>

              <div className="mt-10 pt-7 flex flex-wrap items-center justify-center gap-3"
                   style={{ borderTop: "1px solid var(--rule)" }}>
                <Link href="/console/new" className="btn btn-accent inline-flex">
                  <Icon name="plus" size={14} />
                  Build one from your own files
                </Link>
                <Link href="/console/settings" className="btn inline-flex">
                  <Icon name="database" size={14} />
                  Connect to a database
                </Link>
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="flex flex-col lg:flex-row gap-6 items-stretch lg:items-start">
          <TableRail
            tables={list?.tables ?? null}
            picked={picked}
            query={railQuery}
            onQuery={setRailQuery}
            onPick={(n) => { selectTable(n); touch(n); }}
            pins={pins}
            onTogglePin={togglePin}
            recents={recents}
            listBytes={list?.read_bytes ?? null}
          />

          {/* ---------------------------------------------------------- detail */}
          <section className="flex-1 min-w-0">
            <div className="seg mb-6 flex-wrap">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id as Tab)}
                  data-on={tab === t.id}
                  className="mono !px-3.5 text-[10px] tracking-[0.14em] uppercase"
                >
                  <Icon name={t.icon} size={14} />
                  {t.id}
                  <TabBadge
                    findings={findings}
                    panel={t.id === "insights" ? null : t.id}
                  />
                </button>
              ))}
            </div>

            <div className="panel p-6 min-h-[380px]">
              {tab !== "insights" && <PartialAnalysis d={findings} />}
              {tab === "schema" && (detail
                ? <><SchemaTab d={detail} /><PanelFindings d={findings} panel="schema" /></>
                : <Empty>reading schema…</Empty>)}
              {tab === "versions" && (versions
                ? <><VersionsTab d={versions} /><PanelFindings d={findings} panel="versions" /></>
                : <Empty>reading history…</Empty>)}
              {tab === "indices" && (indices
                ? <><IndicesTab d={indices} /><PanelFindings d={findings} panel="indices" /></>
                : <Empty>reading indices…</Empty>)}
              {tab === "fragments" && (fragments
                ? <><FragmentsTab d={fragments} /><PanelFindings d={findings} panel="fragments" /></>
                : <Empty>reading fragments…</Empty>)}
              {tab === "query" && (picked
                ? <QueryTab key={picked} table={picked} root={root} />
                : <Empty>pick a table to query</Empty>)}
              {tab === "compare" && (picked
                ? <CompareTab key={picked} table={picked} />
                : <Empty>pick a table to compare</Empty>)}
              {tab === "insights" && <InsightsTab d={findings} table={picked} ai={ai} />}
              {tab === "rows" && (
                <RowsTab
                  d={rows}
                  error={rowsError}
                  expanded={expanded}
                  table={picked}
                  ai={ai}
                  onPage={(off) => { setOffset(off); if (picked) loadRows(picked, off, filter, expanded, limit); }}
                  onPageSize={(n) => {
                    // Back to the first page: keeping the offset would leave you
                    // somewhere you did not ask to be, in a page of a new width.
                    setLimit(n); setOffset(0);
                    if (picked) loadRows(picked, 0, filter, expanded, n);
                  }}
                  onFilter={(f) => { setFilter(f); setOffset(0); if (picked) loadRows(picked, 0, f, expanded, limit); }}
                  onExpand={(col) => {
                    const next = expanded.includes(col)
                      ? expanded.filter((c) => c !== col)
                      : [...expanded, col];
                    setExpanded(next);
                    if (picked) loadRows(picked, offset, filter, next, limit);
                  }}
                />
              )}
            </div>
          </section>
        </div>
      )}
    </main>
  );
}

/** How many findings a panel has, when it has any. Zero renders nothing rather than
 *  a zero — a badge that is always there stops being a signal — and the colour is
 *  that panel's own worst severity, not the table's, so a tab holding two notes does
 *  not borrow the alarm from a warning three tabs away. */
function TabBadge({ findings, panel }: { findings: Findings | null; panel: string | null }) {
  const mine = (findings?.findings ?? []).filter((f) => panel === null || f.panel === panel);
  const n = mine.length;
  const warn = mine.some((f) => f.severity === "warn");
  if (!n) return null;
  return (
    <span
      className="mono text-[9px] leading-none px-1.5 py-0.5 rounded-full ml-0.5"
      style={{
        background: warn ? "rgb(var(--video-rgb) / 0.18)" : "rgb(var(--index-rgb) / 0.18)",
        color: warn ? "var(--video)" : "var(--index)",
      }}
    >
      {n}
    </span>
  );
}

/** The table's URI, on demand rather than on screen. You need it to paste into a
 *  script perhaps once a session; you were being shown it continuously. */
function CopyPath({ uri }: { uri: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(uri);
          setDone(true);
          setTimeout(() => setDone(false), 1400);
        } catch {
          // No clipboard permission. The title attribute still carries the path.
        }
      }}
      className="iconbtn !w-7 !h-7"
      title={uri}
      data-tip={done ? "Copied" : "Copy table path"}
      aria-label="Copy table path"
    >
      <Icon name={done ? "check" : "external"} size={13} />
    </button>
  );
}
