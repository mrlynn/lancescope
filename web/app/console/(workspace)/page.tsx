"use client";

import Link from "next/link";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import Icon, { type IconName } from "@/app/components/Icon";
import Mark from "@/app/components/Mark";
import SampleDatasets from "@/app/components/samples/SampleDatasets";
import { Copy, Empty } from "@/app/components/console/atoms";
import {
  FragmentsTab, IndicesTab, SchemaTab, VersionsTab,
} from "@/app/components/console/tabs";
import {
  type Findings,
  getFindings, getFragments, getIndices, getTable, getVersions,
} from "@/app/lib/catalog";
import {
  InsightsTab, PanelFindings, PartialAnalysis,
} from "@/app/components/console/Findings";
import { CompareTab } from "@/app/components/console/CompareTab";
import { DataTab } from "@/app/components/console/DataTab";
import { TrainingTab, trainingFindings } from "@/app/components/console/TrainingTab";
import { QueryTab } from "@/app/components/console/QueryTab";
import { notePush, setParams, useValue } from "@/app/lib/url-state";
import { recordCost, set as setWorkspace, useWorkspace } from "@/app/lib/workspace";

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
    // Last in the group that asks something of the table, and last on purpose: it is
    // the only tab that reads columns, and the only one that spends more than
    // kilobytes. Nothing on it runs until somebody presses a button.
    { id: "data", icon: "rows" },
  ],
];
const TABS = TAB_GROUPS.flat();
type Tab = "schema" | "versions" | "indices" | "fragments" | "query"
  | "compare" | "training" | "insights" | "data";

// Rows was a second query panel: a plain filter box beside one that completed
// columns, an English box the other did not have, and the same grid under both.
// It is one panel now, and this keeps every link that pointed at the old one
// landing on the panel that absorbed it rather than on the schema.
const MERGED_TABS: Record<string, Tab> = { rows: "query" };

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
  const tab: Tab = (TABS.some((x) => x.id === wanted) ? wanted : "schema") as Tab;

  const pickTab = (id: Tab) => {
    // Four views of one table's facts are a view change; a workspace is a place.
    // The settings page draws this line first and in these words: "switching one is
    // a view change rather than a navigation."
    const place = id === "query" || id === "compare" || id === "training";
    setParams({ tab: id }, place ? "push" : "replace");
    if (place) notePush();
  };

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
                        onClick={() => pickTab(t.id as Tab)}
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

