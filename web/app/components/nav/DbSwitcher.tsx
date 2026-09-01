"use client";

/** Which database you are looking at, and how to look at another one.
 *
 *  This replaces the raw root path that used to sit in the header. A path is
 *  provenance, not identity: it is long, it wraps, it is mostly the same prefix
 *  for every database on the machine, and it told you nothing you could act on.
 *  The name is the identity; the path is one hover away, and the whole thing is
 *  also the control that switches.
 */

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import Icon from "@/app/components/Icon";
import { ROOT_SOURCE, dbName, dbParent } from "@/app/lib/dbname";
import type { SettingsState } from "@/app/lib/settings";

export default function DbSwitcher({
  settings,
  root,
  tableCount,
  onSwitch,
}: {
  settings: SettingsState | null;
  root: string | null;
  tableCount?: number;
  onSwitch: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  // Dismiss on anything that means "I am done with this menu". Pointerdown rather
  // than click so a press that starts outside closes it before it lands.
  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("pointerdown", away);
    window.addEventListener("keydown", esc);
    return () => {
      window.removeEventListener("pointerdown", away);
      window.removeEventListener("keydown", esc);
    };
  }, [open]);

  const active = settings?.connections.find((c) => c.active) ?? null;
  const uri = root ?? settings?.root.root ?? null;
  const name = active?.label ?? dbName(uri) ?? "no database";
  const locked = settings?.env_locked ?? false;
  const source = settings ? ROOT_SOURCE[settings.root.source] : "";

  return (
    <div className="relative" ref={box}>
      <button
        onClick={() => setOpen((o) => !o)}
        className={`btn max-w-[280px] ${open ? "btn-on" : ""}`}
        aria-haspopup="menu"
        aria-expanded={open}
        title={uri ? `${uri}${source ? ` — ${source}` : ""}` : "no database configured"}
      >
        <Icon name="database" size={15} />
        <span className="truncate font-medium" style={{ color: open ? undefined : "var(--bright)" }}>
          {name}
        </span>
        {tableCount !== undefined && (
          <span className="mono text-[10px] text-[var(--haze)]">{tableCount}</span>
        )}
        <Icon name="chevronDown" size={13} className={open ? "rotate-180 transition-transform" : "transition-transform"} />
      </button>

      {open && (
        <div
          role="menu"
          className="panel absolute left-0 top-[calc(100%+6px)] z-50 w-[340px] p-1.5 shadow-2xl"
          style={{ boxShadow: "0 18px 40px rgb(0 0 0 / 0.35)" }}
        >
          <div className="eyebrow px-2.5 py-2">
            {locked ? "Pinned by the environment" : "Connections"}
          </div>

          {settings && settings.connections.length > 0 ? (
            settings.connections.map((c) => (
              <button
                key={c.id}
                role="menuitem"
                disabled={locked}
                onClick={() => {
                  if (!c.active) onSwitch(c.id);
                  setOpen(false);
                }}
                className="w-full text-left px-2.5 py-2 rounded-sm flex items-start gap-2.5
                           hover:bg-[rgb(var(--index-rgb)/0.07)] disabled:opacity-50
                           disabled:hover:bg-transparent transition-colors"
              >
                <span className="pt-0.5" style={{ color: c.active ? "var(--video)" : "var(--dim)" }}>
                  <Icon name={c.active ? "check" : "database"} size={14} />
                </span>
                <span className="min-w-0 flex-1">
                  <span
                    className="block text-[13px] truncate"
                    style={{ color: c.active ? "var(--video)" : "var(--bright)" }}
                  >
                    {c.label}
                  </span>
                  <span className="mono block text-[10px] text-[var(--haze)] truncate" title={c.uri}>
                    {dbParent(c.uri) || c.uri}
                  </span>
                  <span className="mono block text-[10px] mt-0.5 text-[var(--dim)]">
                    {c.reachable === false
                      ? <span style={{ color: "var(--video)" }}>unreachable</span>
                      : c.reachable === null
                        ? "unverified"
                        : `${c.tables.length} table${c.tables.length === 1 ? "" : "s"}`}
                  </span>
                </span>
              </button>
            ))
          ) : (
            <p className="text-[12px] text-[var(--haze)] px-2.5 py-3 leading-relaxed">
              {uri
                ? <>Reading <span className="mono text-[var(--bright)]">{uri}</span> — {source}. Nothing saved yet.</>
                : "No connection configured."}
            </p>
          )}

          <div className="h-px my-1.5 bg-[var(--rule)]" />

          {locked && (
            <p className="text-[11px] text-[var(--haze)] px-2.5 pb-2 leading-relaxed">
              <span className="mono text-[var(--bright)]">LANCE_ROOT</span> is set, so it
              wins over anything saved here.
            </p>
          )}

          <Link
            href="/console/settings"
            onClick={() => setOpen(false)}
            className="flex items-center gap-2.5 px-2.5 py-2 rounded-sm text-[13px]
                       text-[var(--body)] hover:text-[var(--bright)]
                       hover:bg-[rgb(var(--index-rgb)/0.07)] transition-colors"
          >
            <Icon name="plus" size={14} />
            Add or manage connections
          </Link>
        </div>
      )}
    </div>
  );
}
