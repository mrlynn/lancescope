"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import Mark from "@/app/components/Mark";
import Wordmark from "@/app/components/Wordmark";
import ThemeToggle from "@/app/components/ThemeToggle";
import { Cost, Empty } from "@/app/components/console/atoms";
import {
  FragmentsTab, IndicesTab, RowsTab, SchemaTab, VersionsTab,
} from "@/app/components/console/tabs";
import { fmtBytes } from "@/app/lib/api";
import {
  ApiError,
  type Fragments, type Indices, type Rows, type TableDetail, type TableList, type Versions,
  getFragments, getIndices, getRows, getTable, getVersions, listTables,
} from "@/app/lib/catalog";
import {
  type SettingsState, activateConnection, getSettings,
} from "@/app/lib/settings";

const TABS = ["schema", "versions", "indices", "fragments", "rows"] as const;
type Tab = (typeof TABS)[number];

const PAGE = 25;

export default function Console() {
  const [list, setList] = useState<TableList | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("schema");

  const [detail, setDetail] = useState<TableDetail | null>(null);
  const [versions, setVersions] = useState<Versions | null>(null);
  const [indices, setIndices] = useState<Indices | null>(null);
  const [fragments, setFragments] = useState<Fragments | null>(null);
  const [rows, setRows] = useState<Rows | null>(null);

  const [offset, setOffset] = useState(0);
  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState<string[]>([]);
  const [rowsError, setRowsError] = useState<string | null>(null);
  const [cost, setCost] = useState<{ bytes: number; iops: number } | null>(null);
  const [settings, setSettings] = useState<SettingsState | null>(null);

  // Everything downstream of the selected table is cleared here rather than in an
  // effect keyed on `picked`: an effect would re-render twice on every click, and
  // the reset is a consequence of the click, not of the state having changed.
  const selectTable = useCallback((name: string | null) => {
    setPicked(name);
    setDetail(null); setVersions(null); setIndices(null); setFragments(null);
    setRows(null); setOffset(0); setFilter(""); setExpanded([]); setRowsError(null);
  }, []);

  const loadTables = useCallback(() => {
    listTables()
      .then((d) => {
        setList(d);
        setPicked((p) => (p && d.tables.some((t) => t.name === p) ? p : d.tables[0]?.name ?? null));
      })
      .catch((e) => setListError(e instanceof Error ? e.message : "unreachable"));
  }, []);

  useEffect(() => {
    loadTables();
    getSettings().then(setSettings).catch(() => setSettings(null));
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
      loadTables();
    } catch (e) {
      setListError(e instanceof Error ? e.message : "could not switch connection");
    }
  }, [loadTables, selectTable]);

  const loadRows = useCallback(
    async (name: string, off: number, f: string, exp: string[]) => {
      try {
        const d = await getRows(name, {
          offset: off, limit: PAGE, filter: f || null, expand: exp,
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
      <header className="flex items-center justify-between gap-4 flex-wrap mb-8">
        <div className="flex items-center gap-4 min-w-0">
          <a href="https://lancedb.com" target="_blank" rel="noreferrer" title="LanceDB"
             className="shrink-0 opacity-90 hover:opacity-100 transition-opacity">
            <Wordmark />
          </a>
          <div className="w-px h-5 bg-[var(--rule)]" />
          <h1 className="text-[19px] font-bold tracking-tight text-[var(--bright)]">Console</h1>
          {settings && settings.connections.length > 1 ? (
            <select
              className="inp mono shrink-0 text-[11px] py-1"
              // `.inp` sets width:100%, and it wins on source order — so the width
              // this control actually needs has to be stated here.
              style={{ width: 190 }}
              value={settings.root.connection_id ?? ""}
              onChange={(e) => switchTo(e.target.value)}
              disabled={settings.env_locked}
              title={settings.env_locked
                ? "LANCE_ROOT is set; connections are inert"
                : `${list?.root ?? ""} — switch connection`}
            >
              {settings.connections.map((c) => (
                <option key={c.id} value={c.id}>{c.label}</option>
              ))}
            </select>
          ) : null}
          <span className="eyebrow normal-case truncate min-w-0 hidden md:block" title={list?.root}>
            {list?.root ?? "…"}
          </span>
        </div>
        <div className="flex items-center gap-4">
          {cost && <Cost bytes={cost.bytes} iops={cost.iops} />}
          <ThemeToggle />
          <Link href="/console/settings" className="pill">Settings</Link>
          <Link href="/"
                className="mono text-[10px] tracking-[0.14em] uppercase px-3 py-1.5 rounded-sm
                           border border-[var(--rule)] text-[var(--haze)]
                           hover:text-[var(--bright)] hover:border-[var(--haze)] transition-colors">
            Demo
          </Link>
        </div>
      </header>

      {listError && (
        <div className="mono text-[12px] px-3.5 py-3 rounded-sm mb-6"
             style={{ background: "rgb(var(--video-rgb) / 0.12)",
                      border: "1px solid rgb(var(--video-rgb) / 0.4)", color: "var(--video)" }}>
          {listError}
        </div>
      )}

      {list && list.tables.length === 0 ? (
        <div className="mt-24 text-center">
          <Mark size={34} className="mx-auto mb-5 text-[var(--rule)]" />
          <p className="text-[15px] text-[var(--haze)] max-w-lg mx-auto leading-relaxed">
            No Lance tables under{" "}
            <span className="mono text-[var(--bright)]">{list.root || "any configured path"}</span>.{" "}
            <Link href="/console/settings" className="underline"
                  style={{ color: "var(--video)" }}>Add a connection</Link>{" "}
            pointing at a LanceDB directory, or build the demo corpus with{" "}
            <span className="mono text-[var(--bright)]">make ingest</span>.
          </p>
        </div>
      ) : (
        <div className="flex flex-col lg:flex-row gap-6 items-stretch lg:items-start">
          {/* ------------------------------------------------------------ rail */}
          <nav className="w-full lg:w-[248px] shrink-0 space-y-1.5">
            <div className="eyebrow mb-3">
              {list ? `${list.tables.length} table${list.tables.length === 1 ? "" : "s"}` : "loading"}
            </div>
            {list?.tables.map((t) => {
              const on = t.name === picked;
              return (
                <button
                  key={t.name}
                  onClick={() => selectTable(t.name)}
                  className="w-full text-left px-3.5 py-3 rounded-sm border transition-colors"
                  style={
                    on
                      ? { borderColor: "var(--video)", background: "rgb(var(--video-rgb) / 0.09)" }
                      : { borderColor: "var(--rule)" }
                  }
                >
                  <div className="mono text-[13px] mb-1"
                       style={{ color: on ? "var(--video)" : "var(--bright)" }}>
                    {t.name}
                  </div>
                  <div className="mono text-[10px] text-[var(--haze)]">
                    {t.rows.toLocaleString()} rows · {t.columns} cols · v{t.version}
                  </div>
                  {t.blob_columns.length > 0 && (
                    <div className="mono text-[10px] mt-1" style={{ color: "var(--video)" }}>
                      {t.blob_columns.length} blob column
                      {t.blob_columns.length === 1 ? "" : "s"}
                    </div>
                  )}
                </button>
              );
            })}
            {list && (
              <p className="text-[11px] text-[var(--haze)] leading-relaxed pt-3">
                Listing every table cost{" "}
                <span className="mono" style={{ color: "var(--index)" }}>
                  {fmtBytes(list.read_bytes).value} {fmtBytes(list.read_bytes).unit}
                </span>
                . It reads manifests, never data.
              </p>
            )}
          </nav>

          {/* ---------------------------------------------------------- detail */}
          <section className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-6 flex-wrap">
              {TABS.map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className="mono text-[10px] tracking-[0.14em] uppercase px-3.5 py-2
                             rounded-sm border transition-colors"
                  style={
                    tab === t
                      ? { borderColor: "var(--video)", color: "var(--video)",
                          background: "rgb(var(--video-rgb) / 0.09)" }
                      : { borderColor: "var(--rule)", color: "var(--haze)" }
                  }
                >
                  {t}
                </button>
              ))}
              {current && (
                <span className="eyebrow ml-2 normal-case truncate min-w-0 hidden xl:block"
                      title={current.uri}>
                  {current.uri}
                </span>
              )}
            </div>

            <div className="panel p-6 min-h-[380px]">
              {tab === "schema" && (detail ? <SchemaTab d={detail} /> : <Empty>reading schema…</Empty>)}
              {tab === "versions" && (versions ? <VersionsTab d={versions} /> : <Empty>reading history…</Empty>)}
              {tab === "indices" && (indices ? <IndicesTab d={indices} /> : <Empty>reading indices…</Empty>)}
              {tab === "fragments" && (fragments ? <FragmentsTab d={fragments} /> : <Empty>reading fragments…</Empty>)}
              {tab === "rows" && (
                <RowsTab
                  d={rows}
                  error={rowsError}
                  expanded={expanded}
                  onPage={(off) => { setOffset(off); if (picked) loadRows(picked, off, filter, expanded); }}
                  onFilter={(f) => { setFilter(f); setOffset(0); if (picked) loadRows(picked, 0, f, expanded); }}
                  onExpand={(col) => {
                    const next = expanded.includes(col)
                      ? expanded.filter((c) => c !== col)
                      : [...expanded, col];
                    setExpanded(next);
                    if (picked) loadRows(picked, offset, filter, next);
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
