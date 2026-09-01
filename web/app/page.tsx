"use client";

/** Home.
 *
 *  `/` used to be the conference demo, which made the demo look like the product.
 *  It is one thing this app does. The app is a console for reading LanceDB
 *  datasets, and its home is the place that says which database you are attached
 *  to and gets you into it in one click — with the demo alongside as an equal, not
 *  as the front door.
 *
 *  Everything on this page is live. A home screen that lists features it cannot
 *  confirm are working is a brochure; this one says how many tables are actually
 *  there, and says so when there are none.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import Icon, { type IconName } from "@/app/components/Icon";
import Mark from "@/app/components/Mark";
import AppBar from "@/app/components/nav/AppBar";
import { fmtBytes } from "@/app/lib/api";
import { type TableList, listTables } from "@/app/lib/catalog";
import { ROOT_SOURCE, dbName } from "@/app/lib/dbname";
import { useRecents } from "@/app/lib/recents";
import { type SettingsState, getSettings } from "@/app/lib/settings";

type Health = { ok: boolean; moments: number; talks: number };

export default function Home() {
  const [list, setList] = useState<TableList | null>(null);
  const [settings, setSettings] = useState<SettingsState | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [reachable, setReachable] = useState<boolean | null>(null);

  useEffect(() => {
    listTables().then((d) => { setList(d); setReachable(true); })
      .catch(() => setReachable(false));
    getSettings().then(setSettings).catch(() => setSettings(null));
    fetch("/api/health", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  const root = list?.root ?? settings?.root.root ?? null;
  const { recents } = useRecents(root);
  const active = settings?.connections.find((c) => c.active) ?? null;
  const name = active?.label ?? dbName(root);
  const tables = list?.tables.length ?? 0;
  const demoReady = health?.ok ?? false;

  return (
    <main className="relative z-10 min-h-screen px-[var(--stage-pad)] pt-7 pb-20">
      <AppBar crumbs={[]} />

      {/* ------------------------------------------------------------- masthead */}
      <section className="max-w-[720px] mt-6 mb-12">
        <div className="flex items-center gap-3 mb-4">
          <Mark size={26} className="text-[var(--video)]" />
          <h1 className="text-[34px] leading-none font-black tracking-tight text-[var(--bright)]">
            LanceScope
          </h1>
        </div>
        <p className="text-[16px] leading-relaxed text-[var(--body)]">
          See what is actually inside a LanceDB dataset — its schema, versions,
          indices, fragments and rows — with the byte cost of every read shown as you
          go. Read&#8209;only, by construction: nothing here writes to a table.
        </p>
      </section>

      {/* --------------------------------------------------------- what is bound */}
      <section className="panel p-5 mb-6 flex items-center gap-4 flex-wrap">
        <span style={{ color: reachable === false ? "var(--video)" : "var(--index)" }}>
          <Icon name={reachable === false ? "warning" : "database"} size={20} />
        </span>
        <div className="min-w-0 flex-1">
          {reachable === false ? (
            <>
              <div className="text-[14px] text-[var(--bright)]">The API is not answering</div>
              <div className="mono text-[11px] text-[var(--haze)] mt-1">
                Start it with <span className="text-[var(--bright)]">make api</span>, or run
                both halves with <span className="text-[var(--bright)]">make dev</span>.
              </div>
            </>
          ) : root ? (
            <>
              <div className="text-[14px] text-[var(--bright)]">
                {name}
                <span className="text-[var(--haze)] font-normal">
                  {" — "}{tables} table{tables === 1 ? "" : "s"}
                  {list && <>, listed for {fmtBytes(list.read_bytes).value} {fmtBytes(list.read_bytes).unit}</>}
                </span>
              </div>
              <div className="mono flex flex-wrap items-baseline gap-x-2 text-[11px] text-[var(--haze)] mt-1">
                <span className="truncate max-w-full" title={root}>{root}</span>
                {settings && (
                  <span className="text-[var(--dim)]">· {ROOT_SOURCE[settings.root.source]}</span>
                )}
              </div>
            </>
          ) : (
            <>
              <div className="text-[14px] text-[var(--bright)]">No database connected</div>
              <div className="mono text-[11px] text-[var(--haze)] mt-1">
                Point the console at any directory holding <span className="text-[var(--bright)]">.lance</span> tables.
              </div>
            </>
          )}
        </div>
        <Link href="/console/settings" className="btn shrink-0">
          <Icon name="settings" size={15} />
          {root ? "Connections" : "Connect a database"}
        </Link>
      </section>

      {/* ------------------------------------------------------------------ ways in */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-10">
        <Card
          href="/console"
          icon="table"
          title="Console"
          accent
          body={root
            ? `Browse ${tables} table${tables === 1 ? "" : "s"} — schema, history, indices, fragments and rows.`
            : "Browse a dataset's schema, history, indices, fragments and rows."}
          foot={list ? `${tables} table${tables === 1 ? "" : "s"} ready` : "checking…"}
        />
        <Card
          href="/demo"
          icon="play"
          title="Ctrl-F for Video"
          body="Multimodal search over conference talks, where the video and its index are the same table."
          foot={health === null
            ? "checking…"
            : demoReady
              ? `${health.moments.toLocaleString()} moments · ${health.talks} talks`
              : "corpus not built — make ingest"}
          dim={health !== null && !demoReady}
        />
        <Card
          href="/console/settings"
          icon="settings"
          title="Settings"
          body="Connections, and the optional intelligence layer that powers plain-English filters and summaries."
          foot={settings
            ? `${settings.connections.length} connection${settings.connections.length === 1 ? "" : "s"} · ${settings.intelligence.provider}`
            : "checking…"}
        />
      </div>

      {/* ------------------------------------------------------------------ recents */}
      {recents.length > 0 && (
        <section>
          <div className="eyebrow flex items-center gap-2 mb-3">
            <Icon name="clock" size={12} />
            Recent tables
          </div>
          <div className="flex flex-wrap gap-2">
            {recents.map((n) => (
              <Link key={n} href={`/console?table=${encodeURIComponent(n)}`} className="btn">
                <Icon name="table" size={14} />
                <span className="mono text-[12px]">{n}</span>
              </Link>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}

function Card({ href, icon, title, body, foot, accent = false, dim = false }: {
  href: string;
  icon: IconName;
  title: string;
  body: string;
  foot: string;
  accent?: boolean;
  dim?: boolean;
}) {
  return (
    <Link
      href={href}
      className="panel p-5 flex flex-col gap-3 group transition-colors"
      style={accent ? { borderColor: "rgb(var(--video-rgb) / 0.45)" } : undefined}
    >
      <span
        className="w-9 h-9 grid place-items-center rounded-sm transition-colors"
        style={{
          color: accent ? "var(--video)" : "var(--haze)",
          background: accent ? "rgb(var(--video-rgb) / 0.11)" : "rgb(var(--index-rgb) / 0.08)",
        }}
      >
        <Icon name={icon} size={19} />
      </span>
      <span className="flex items-center gap-2 text-[16px] font-bold tracking-tight text-[var(--bright)]">
        {title}
        <span className="text-[var(--dim)] group-hover:text-[var(--video)] transition-colors">
          <Icon name="arrowRight" size={15} />
        </span>
      </span>
      <span className="text-[13px] leading-relaxed text-[var(--body)] flex-1">{body}</span>
      <span className="mono text-[10px]" style={{ color: dim ? "var(--dim)" : "var(--haze)" }}>
        {foot}
      </span>
    </Link>
  );
}
