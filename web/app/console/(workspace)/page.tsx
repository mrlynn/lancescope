"use client";

import Link from "next/link";
import { useCallback, useEffect } from "react";
import Icon, { type IconName } from "@/app/components/Icon";
import Mark from "@/app/components/Mark";
import SampleDatasets from "@/app/components/samples/SampleDatasets";
import { Copy, Empty } from "@/app/components/console/atoms";
import {
  FragmentsTab, IndicesTab, SchemaTab, VersionsTab,
} from "@/app/components/console/tabs";
import {
  type Finding, type Findings,
  getFindings, getFragments, getIndices, getTable, getVersions,
} from "@/app/lib/catalog";
import { PanelFindings, PartialAnalysis } from "@/app/components/console/Findings";
import { CompareTab } from "@/app/components/console/CompareTab";
import { DataTab } from "@/app/components/console/DataTab";
import { TrainingTab, trainingFindings } from "@/app/components/console/TrainingTab";
import { QueryTab } from "@/app/components/console/QueryTab";
import { useShortcut } from "@/app/lib/keys";
import { notePush, setParams, useValue } from "@/app/lib/url-state";
import { recordCost, set as setWorkspace, useWorkspace } from "@/app/lib/workspace";

// Five screens, and one of them has sections.
//
// The old strip was nine peers in one row, and it took a `ResizeObserver`, a
// hysteresis band, two fade masks and a pair of nudge arrows to keep them there.
// They were never nine of the same thing. Four of them read the table's own
// metadata — its shape, its history, its indices, its layout — and are four views
// of one document. The rest each ask the table something, and cost something
// different to ask.
//
// So the four become sections of a Table screen, which is the case the strip was
// always fine for: tightly related facts about one thing. The others become places.
const SECTIONS: { id: Section; icon: IconName }[] = [
  { id: "schema", icon: "schema" },
  { id: "versions", icon: "history" },
  { id: "indices", icon: "index" },
  { id: "fragments", icon: "fragments" },
];

const SCREENS: { id: Screen; icon: IconName }[] = [
  { id: "table", icon: "table" },
  { id: "query", icon: "search" },
  { id: "compare", icon: "history" },
  { id: "training", icon: "play" },
  // Last, and last on purpose: it is the only screen that reads columns, and the
  // only one that spends more than kilobytes. Nothing on it runs until somebody
  // presses a button.
  { id: "data", icon: "rows" },
];

type Section = "schema" | "versions" | "indices" | "fragments";
type Screen = "table" | "query" | "compare" | "training" | "data";
type Tab = Section | "query" | "compare" | "training" | "data";

const TABS: Tab[] = ["schema", "versions", "indices", "fragments",
                     "query", "compare", "training", "data"];

/** Which screen a view belongs to. The URL still names the view, not the screen —
 *  see `MERGED_TABS`. */
function screenOf(tab: Tab): Screen {
  return SECTIONS.some((s) => s.id === tab) ? "table" : (tab as Screen);
}

// Every `?tab=` this console has ever answered to still lands somewhere real.
//
// `rows` was a second query panel and was folded into the one that completes column
// names. `insights` was the collected findings list; it is the inspector now, which
// is open beside whatever you are looking at — so the link opens the table it was
// about and the findings are already on screen. Five links in this repository point
// at one or the other.
const MERGED_TABS: Record<string, Tab> = { rows: "query", insights: "schema" };

export default function Console() {
  // What was fetched lives in the store, because the shell shows some of it and a
  // page that owned it would take it away on every navigation. Where you are lives
  // in the URL, because every one of these should survive being pasted to somebody.
  const w = useWorkspace();
  const picked = useValue("table");
  // `?tab=` may be absent, may name a tab that was merged into another, and may name
  // one that never existed — an old link, or a typo. All three land on the schema
  // rather than on a blank panel. `MERGED_TABS` is the existing redirect and keeps
  // `?tab=rows` working, which five links in this repository still use.
  const asked = useValue("tab");
  const wanted = asked ? MERGED_TABS[asked] ?? asked : null;
  const tab: Tab = (TABS.includes(wanted as Tab) ? wanted : "schema") as Tab;
  const screen = screenOf(tab);

  const pickTab = useCallback((id: Tab) => {
    // Changing screen is going somewhere; changing section is looking at another
    // part of what you are already on. The settings page drew this line first and in
    // these words: "switching one is a view change rather than a navigation."
    const place = screenOf(id) !== screenOf(tab);
    setParams({ tab: id }, place ? "push" : "replace");
    if (place) notePush();
  }, [tab]);

  // ⌘1..⌘5, in the order the strip reads. Registered by the page rather than the
  // shell, because the screens are the page's vocabulary and the shell should not
  // have to know how many there are.
  const goScreen = useCallback((n: number) => {
    const s = SCREENS[n];
    if (s) pickTab(s.id === "table" ? "schema" : (s.id as Tab));
  }, [pickTab]);
  useShortcut("screen-1", useCallback(() => goScreen(0), [goScreen]));
  useShortcut("screen-2", useCallback(() => goScreen(1), [goScreen]));
  useShortcut("screen-3", useCallback(() => goScreen(2), [goScreen]));
  useShortcut("screen-4", useCallback(() => goScreen(3), [goScreen]));
  useShortcut("screen-5", useCallback(() => goScreen(4), [goScreen]));


  const { list, listError, detail, versions, indices, fragments, findings, ai,
          demoReady } = w;
  const root = list?.root ?? w.settings?.root.root ?? null;

  // The table has to exist before it can be opened. `?table=` may name one that
  // was renamed or belongs to a database this is no longer pointed at, and landing
  // on the first table is a better answer than an empty panel with no explanation.
  useEffect(() => {
    if (!list) return;
    const known = (n: string | null) => !!n && list.tables.some((x) => x.name === n);
    if (!known(picked) && list.tables.length > 0) {
      // Replace, not push: this is a correction to where you already are, and a
      // back button that returns you to a table that does not exist is a trap.
      setParams({ table: list.tables[0].name }, "replace");
    }
  }, [list, picked]);

  // Once per table, not once per tab: the same list renders inline under four
  // panels, is collected in the inspector, and is one cheap metadata read.
  useEffect(() => {
    if (!picked) return;
    let alive = true;
    getFindings(picked)
      .then((d) => { if (alive) setWorkspace({ findings: d }); })
      .catch(() => { if (alive) setWorkspace({ findings: null }); });
    return () => { alive = false; };
  }, [picked]);

  // Only what the open tab needs, and only if it is not already held.
  useEffect(() => {
    if (!picked) return;
    let alive = true;
    const run = async () => {
      try {
        if ((tab === "schema" || tab === "training") && !detail) {
          const d = await getTable(picked);
          if (!alive) return;
          setWorkspace({ detail: d });
          recordCost(`schema · ${picked}`, d.read_bytes, d.read_iops);
        } else if (tab === "versions" && !versions) {
          const d = await getVersions(picked);
          if (!alive) return;
          setWorkspace({ versions: d });
          recordCost(`versions · ${picked}`, d.read_bytes, d.read_iops);
        } else if (tab === "indices" && !indices) {
          const d = await getIndices(picked);
          if (!alive) return;
          setWorkspace({ indices: d });
          recordCost(`indices · ${picked}`, d.read_bytes, d.read_iops);
        } else if (tab === "fragments" && !fragments) {
          const d = await getFragments(picked);
          if (!alive) return;
          setWorkspace({ fragments: d });
          recordCost(`fragments · ${picked}`, d.read_bytes, d.read_iops);
        }
      } catch (e) {
        if (alive) {
          setWorkspace({ listError: e instanceof Error ? e.message : "request failed" });
        }
      }
    };
    run();
    return () => { alive = false; };
  }, [picked, tab, detail, versions, indices, fragments]);

  const current = list?.tables.find((t) => t.name === picked) ?? null;

  return (
    <>
      {/* The table the centre is about, with its path. The rail says which table is
          selected and the inspector says what version it is on; this is the one
          thing neither of them carries, because a path is for pasting. */}
      {current && (
        <div className="flex items-center gap-2 mb-5">
          <span className="mono text-[13px] text-[var(--bright)] truncate">{current.name}</span>
          <Copy size={13} className="!w-7 !h-7" what="table path" title={current.uri}
                value={current.uri} />
          {demoReady && (
            <Link href="/demo" className="iconbtn !w-7 !h-7 ml-auto" data-tip="Ctrl-F for Video"
                  aria-label="Open the demo">
              <Icon name="play" size={14} />
            </Link>
          )}
        </div>
      )}


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
                {/* Two ways out, and which one applies depends on the scheme rather
                    than on anything this component can see — so both are offered and
                    the reason above says which is the shorter road. */}
                Install an adapter for this scheme, or{" "}
                <Link href="/console/settings" className="underline"
                      style={{ color: "var(--video)" }}>switch to another database</Link>.
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
        <div className="min-w-0">
          <section className="min-w-0">
            {/* Screens, then — on the one that has them — its sections. Two rows
                of at most five, which cannot overflow at any width this supports,
                so the whole scroll-and-fade apparatus the nine-tab strip needed is
                gone with it. */}
            <div className="seg mb-3" role="tablist" aria-label="Screen">
              {SCREENS.map((s) => (
                <button
                  key={s.id}
                  role="tab"
                  aria-selected={screen === s.id}
                  data-on={screen === s.id}
                  // A screen is entered at its first section; a section is only
                  // meaningful inside the table screen.
                  onClick={() => pickTab(s.id === "table" ? "schema" : (s.id as Tab))}
                  className="mono !px-3 text-[10px] tracking-[0.14em] uppercase"
                >
                  <Icon name={s.icon} size={14} />
                  <span>{s.id}</span>
                  <ScreenBadge findings={findings} screen={s.id} />
                </button>
              ))}
            </div>

            {screen === "table" && (
              <div className="seg mb-6" role="tablist" aria-label="Section">
                {SECTIONS.map((s) => (
                  <button
                    key={s.id}
                    role="tab"
                    aria-selected={tab === s.id}
                    data-on={tab === s.id}
                    onClick={() => pickTab(s.id)}
                    className="mono !px-3 text-[10px] tracking-[0.14em] uppercase"
                  >
                    <Icon name={s.icon} size={14} />
                    <span>{s.id}</span>
                    <TabBadge findings={findings} panel={s.id} />
                  </button>
                ))}
              </div>
            )}

            <div className="panel p-6 min-h-[380px]">
              <PartialAnalysis d={findings} />
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
              {tab === "data" && (picked
                ? <DataTab key={picked} table={picked} />
                : <Empty>pick a table to check</Empty>)}
            </div>
          </section>
        </div>
      )}
    </>
  );
}

/** How many findings a whole screen has.
 *
 *  The table screen carries whatever its four sections carry, because a count that
 *  vanished when you left the section holding it would be a count nobody could act
 *  on. Training carries its facet rather than a panel — the same rules narrowed to
 *  what a training run pays for. Query, compare and data carry nothing: no rule
 *  fires about them, and a badge that is always absent is not a badge.
 */
function ScreenBadge({ findings, screen }: { findings: Findings | null; screen: Screen }) {
  if (screen === "training") return <TabBadge findings={findings} panel={null} facet="training" />;
  if (screen !== "table") return null;
  const sections = new Set<string>(SECTIONS.map((s) => s.id));
  return <TabBadge findings={findings} panel={null} only={(f) => sections.has(f.panel)} />;
}

/** How many findings a panel has, when it has any. Zero renders nothing rather than
 *  a zero — a badge that is always there stops being a signal — and the colour is
 *  that panel's own worst severity, not the table's, so a tab holding two notes does
 *  not borrow the alarm from a warning three tabs away. */
function TabBadge({ findings, panel, facet, only }: {
  findings: Findings | null;
  panel: string | null;
  facet?: "training";
  /** A predicate instead of one panel, for a screen that gathers several. */
  only?: (f: Finding) => boolean;
}) {
  const mine = facet
    ? trainingFindings(findings)
    : (findings?.findings ?? []).filter(
        (f) => (only ? only(f) : panel === null || f.panel === panel),
      );
  const n = mine.length;
  const warn = mine.some((f) => f.severity === "warn");
  if (!n) return null;
  return (
    <span
      className="mono text-[9px] leading-none px-1.5 py-0.5 rounded-full ml-0.5"
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

