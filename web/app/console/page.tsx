"use client";

import Link from "next/link";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import Icon, { type IconName } from "@/app/components/Icon";
import Mark from "@/app/components/Mark";
import AppBar from "@/app/components/nav/AppBar";
import DbSwitcher from "@/app/components/nav/DbSwitcher";
import SampleDatasets from "@/app/components/samples/SampleDatasets";
import { Copy, Cost, Empty } from "@/app/components/console/atoms";
import TableRail from "@/app/components/console/TableRail";
import {
  FragmentsTab, IndicesTab, SchemaTab, VersionsTab,
} from "@/app/components/console/tabs";
import {
  type Findings, type Fragments, type Indices, type TableDetail,
  type TableList, type Versions,
  getFindings, getFragments, getIndices, getTable, getVersions, listTables,
} from "@/app/lib/catalog";
import {
  InsightsTab, PanelFindings, PartialAnalysis,
} from "@/app/components/console/Findings";
import { CompareTab } from "@/app/components/console/CompareTab";
import { TrainingTab, trainingFindings } from "@/app/components/console/TrainingTab";
import { QueryTab } from "@/app/components/console/QueryTab";
import { usePins, useRecents } from "@/app/lib/recents";
import {
  type Capabilities, type SettingsState, activateConnection, getCapabilities,
  getSettings,
} from "@/app/lib/settings";

// Each tab names what it reads, and carries the glyph for it — the row is
// scannable as shapes before any of the words are read.
//
// Two groups, because the tabs are two kinds of thing: the first five read the
// table's own metadata, the last four ask something of it. The gap between the
// groups is the seam, and it is the only one — the strip stays a single row at
// every width and scrolls when nine tabs no longer fit, rather than wrapping the
// second group onto a line of its own and moving the nav under the reader.
const TAB_GROUPS: { id: string; icon: IconName }[][] = [
  [
    { id: "schema", icon: "schema" },
    { id: "versions", icon: "history" },
    { id: "indices", icon: "index" },
    { id: "fragments", icon: "fragments" },
  ],
  [
    { id: "query", icon: "search" },
    { id: "compare", icon: "history" },
    { id: "training", icon: "play" },
    { id: "insights", icon: "spark" },
  ],
];
const TABS = TAB_GROUPS.flat();
type Tab = "schema" | "versions" | "indices" | "fragments" | "query"
  | "compare" | "training" | "insights";

// Rows was a second query panel: a plain filter box beside one that completed
// columns, an English box the other did not have, and the same grid under both.
// It is one panel now, and this keeps every link that pointed at the old one
// landing on the panel that absorbed it rather than on the schema.
const MERGED_TABS: Record<string, Tab> = { rows: "query" };

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
  // Fetched once per table rather than per tab: the same list is rendered inline
  // under four panels and collected in Insights, and it is one cheap metadata read.
  const [findings, setFindings] = useState<Findings | null>(null);

  const [cost, setCost] = useState<{ bytes: number; iops: number } | null>(null);
  const [settings, setSettings] = useState<SettingsState | null>(null);
  // What the language layer can do right now, so the console offers it only when it
  // is actually there — and stays exactly as useful when it is not.
  const [ai, setAi] = useState<Capabilities | null>(null);
  // Whether the demo corpus is under this root, on the same terms as `ai` above:
  // the console links to the demo only where there is a demo to link to. The
  // corpus is 2.5 GB and is not shipped, so in most builds there is not one, and
  // an unconditional link there is a promise the next page cannot keep.
  const [demoReady, setDemoReady] = useState(false);

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
        //
        // `picked` is only set when `?table=` names a table that exists, but a bare
        // `?tab=insights` is still a meaningful request: the console falls back to the
        // first table, and the asked-for tab is the right one to open it on. Gating the
        // tab on a named table made that link land on the schema instead, silently.
        const asked = wantTab ? MERGED_TABS[wantTab] ?? wantTab : null;
        if (asked && d.tables.length > 0 && TABS.some((x) => x.id === asked)) {
          setTab(asked as Tab);
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
    fetch("/api/health", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setDemoReady(Boolean(d?.ok)))
      .catch(() => setDemoReady(false));
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
        if ((tab === "schema" || tab === "training") && !detail) {
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
        }
      } catch (e) {
        if (alive) setListError(e instanceof Error ? e.message : "request failed");
      }
    };
    run();
    return () => { alive = false; };
  }, [picked, tab, detail, versions, indices, fragments]);

  // Nine tabs against a narrow window, in the order the strip gives things up.
  //
  // Full labels while they fit. When they stop fitting the labels go and the
  // glyphs stay — every tab already carries one, and nine icon buttons fit a
  // phone with room over, so the whole nav stays on screen and nothing is cut.
  // Only if even that overflows does the strip scroll, and then it says so: the
  // overflowing edge fades and grows an arrow to push it with. A strip that
  // scrolls with no edge to grab is a nav that hides half of itself and does not
  // admit it, which is what the wrapping row was replaced with the first time.
  const tabbar = useRef<HTMLDivElement>(null);
  const [compact, setCompact] = useState(false);
  const [more, setMore] = useState({ left: false, right: false });
  // What the labelled strip measured last time it was labelled. Kept because the
  // question on the way back up — would the labels fit again? — cannot be asked of
  // a strip that is currently wearing none. Badges change it, so a table's
  // findings clear it rather than answer for the next table.
  const labelledWidth = useRef(0);
  useLayoutEffect(() => { labelledWidth.current = 0; }, [findings]);

  useLayoutEffect(() => {
    const bar = tabbar.current;
    if (!bar) return;
    const fit = () => {
      const room = bar.clientWidth;
      if (!compact) {
        labelledWidth.current = bar.scrollWidth;
        if (bar.scrollWidth > room + 1) setCompact(true);
      } else if (!labelledWidth.current || room >= labelledWidth.current + 8) {
        // Back to labels either because they now fit, or because a new table's
        // badges changed what "fit" means and the only way to measure a labelled
        // strip is to be one. Both happen inside a layout effect, so the widened
        // strip is measured and re-compacted, if it has to be, before it is painted.
        //
        // 8px of hysteresis: a threshold the strip lands exactly on would flip back
        // and forth on every pixel of a drag.
        setCompact(false);
      }
      setMore({
        left: bar.scrollLeft > 1,
        right: bar.scrollLeft + room < bar.scrollWidth - 1,
      });
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(bar);
    bar.addEventListener("scroll", fit, { passive: true });
    return () => { ro.disconnect(); bar.removeEventListener("scroll", fit); };
  }, [compact, findings, tab]);

  // The open tab can still sit past the edge on a strip that scrolls — a nav that
  // does not show where you are. Nudge it back when it is out of view, and only
  // along the strip: scrolling the page under someone who just pressed a button is
  // a worse surprise than the one this is fixing.
  useEffect(() => {
    const bar = tabbar.current;
    const on = bar?.querySelector<HTMLElement>('button[data-on="true"]');
    if (!bar || !on) return;
    const b = bar.getBoundingClientRect();
    const o = on.getBoundingClientRect();
    if (o.left >= b.left && o.right <= b.right) return;
    bar.scrollTo({
      left: Math.max(0, bar.scrollLeft + (o.left - b.left) - (b.width - o.width) / 2),
      behavior: "smooth",
    });
  }, [tab, compact]);

  const nudge = (dir: -1 | 1) => {
    const bar = tabbar.current;
    if (!bar) return;
    bar.scrollBy({ left: dir * bar.clientWidth * 0.6, behavior: "smooth" });
  };

  const current = list?.tables.find((t) => t.name === picked) ?? null;

  return (
    <main className="relative z-10 min-h-screen px-[var(--stage-pad)] pt-7 pb-16">
      <AppBar crumbs={[{ label: "Console" }]}>
        {cost && <Cost bytes={cost.bytes} iops={cost.iops} />}
        <Link href="/console/new" className="iconbtn" data-tip="Build one from files"
              aria-label="Build a database from files">
          <Icon name="plus" size={16} />
        </Link>
        {demoReady && (
          <Link href="/demo" className="iconbtn" data-tip="Ctrl-F for Video" aria-label="Open the demo">
            <Icon name="play" size={16} />
          </Link>
        )}
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
            <Copy size={13} className="!w-7 !h-7" what="table path" title={current.uri} value={current.uri} />
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
          {/* Three different facts that used to render as the same sentence: a
              database with nothing in it, a connection this tool cannot read, and a
              root it can normally read and could not reach this time. The first is
              about the data; the second is about us; the third is about neither and
              usually stops being true on its own. */}
          {list.listing_error ? (
            <div className="max-w-xl mx-auto">
              <p className="text-[15px] text-[var(--bright)] leading-relaxed">
                This database could not be listed.
              </p>
              <p className="text-[14px] text-[var(--haze)] leading-relaxed mt-3">
                {list.listing_error}
              </p>
              <p className="mono text-[12px] text-[var(--haze)] mt-4 break-all">
                {list.root}
              </p>
              {/* Said plainly, because the previous version of this screen said the
                  database was empty and sent people looking for a problem with
                  their data. A remote root is one network call away and that call
                  can be refused; nothing here is known to be wrong with the table. */}
              <p className="text-[13px] text-[var(--haze)] leading-relaxed mt-4">
                Nothing is known to be wrong with the tables — this is about
                reaching them. A remote root is read over the network, and the host
                serving it can refuse or rate limit a request.
              </p>
              <button className="btn mt-5" onClick={() => window.location.reload()}>
                <Icon name="refresh" size={14} />
                Try again
              </button>
            </div>
          ) : list.capabilities && !list.capabilities.discover.available ? (
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
            <div className="tabstrip mb-6" data-more-left={more.left} data-more-right={more.right}>
              <button
                className="tabstrip-arrow" data-side="left" tabIndex={-1} aria-hidden={!more.left}
                onClick={() => nudge(-1)} aria-label="Scroll the tabs left"
              >
                <Icon name="chevronLeft" size={14} />
              </button>
              <div className="tabbar" ref={tabbar} data-compact={compact}>
                {TAB_GROUPS.map((group) => (
                  <div key={group[0].id} className="seg">
                    {group.map((t) => (
                      <button
                        key={t.id}
                        onClick={() => setTab(t.id as Tab)}
                        data-on={tab === t.id}
                        // The label is the accessible name while it is on screen and
                        // the tooltip once it is not, so a glyph-only strip still
                        // says what each button reads.
                        aria-label={t.id}
                        title={compact ? t.id : undefined}
                        className="mono !px-3 text-[10px] tracking-[0.14em] uppercase"
                      >
                        <Icon name={t.icon} size={14} />
                        <span className="tab-label">{t.id}</span>
                        <TabBadge
                          findings={findings}
                          panel={t.id === "insights" ? null : t.id}
                          facet={t.id === "training" ? "training" : undefined}
                        />
                      </button>
                    ))}
                  </div>
                ))}
              </div>
              <button
                className="tabstrip-arrow" data-side="right" tabIndex={-1} aria-hidden={!more.right}
                onClick={() => nudge(1)} aria-label="Scroll the tabs right"
              >
                <Icon name="chevronRight" size={14} />
              </button>
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
                ? <QueryTab key={picked} table={picked} root={root} ai={ai} />
                : <Empty>pick a table to query</Empty>)}
              {tab === "training" && <TrainingTab d={detail} findings={findings} />}
              {tab === "compare" && (picked
                ? <CompareTab key={picked} table={picked} />
                : <Empty>pick a table to compare</Empty>)}
              {tab === "insights" && <InsightsTab d={findings} table={picked} ai={ai} />}
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
function TabBadge({ findings, panel, facet }: {
  findings: Findings | null; panel: string | null; facet?: "training";
}) {
  const mine = facet
    ? trainingFindings(findings)
    : (findings?.findings ?? []).filter((f) => panel === null || f.panel === panel);
  const n = mine.length;
  const warn = mine.some((f) => f.severity === "warn");
  if (!n) return null;
  return (
    <span
      className="tab-badge mono text-[9px] leading-none px-1.5 py-0.5 rounded-full ml-0.5"
      // An opaque ground rather than a tint. The badge sits on the active tab,
      // which is already a 0.12 wash of the same accent, and tints compose: a
      // 0.18 pill over a 0.12 tab put 9px text on an effective 0.28 of its own
      // colour, at 3.8:1. The ring keeps it reading as accent-coloured without
      // putting any of that colour behind the digit.
      style={{
        background: "var(--ink-2)",
        boxShadow: `inset 0 0 0 1px rgb(var(--${warn ? "video" : "index"}-rgb) / 0.5)`,
        color: warn ? "var(--video)" : "var(--index)",
      }}
    >
      {n}
    </span>
  );
}

