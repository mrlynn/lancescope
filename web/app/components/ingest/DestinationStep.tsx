"use client";

/** Naming the database, and showing where it will actually land.
 *
 *  The user types a *name*; the parent directory is chosen for them and shown in
 *  full before anything runs. Asking someone to paste two paths to create one table
 *  is a way of making them read their own filesystem out loud, and the first one is
 *  a decision the tool can make well: beside the database they already have, else a
 *  plainly named folder in their home directory.
 */

import Icon from "@/app/components/Icon";
import type { ResolvedEmbedder } from "@/app/lib/ingest";

export function DestinationStep({
  name, onName, parent, onParent, defaultParent, embedder, advanced, onAdvanced,
}: {
  name: string;
  onName: (v: string) => void;
  parent: string;
  onParent: (v: string) => void;
  defaultParent: string;
  embedder: ResolvedEmbedder | null;
  advanced: boolean;
  onAdvanced: (v: boolean) => void;
}) {
  const resolved = `${(parent || defaultParent).replace(/\/$/, "")}/${name || "…"}.lance`;
  const bad = name && !/^[A-Za-z0-9_-]+$/.test(name);

  return (
    <section>
      <div className="eyebrow mb-3">new database</div>

      <div className="flex flex-wrap gap-2 items-center">
        <input
          className="inp mono min-w-[16rem]"
          placeholder="photos"
          value={name}
          spellCheck={false}
          onChange={(e) => onName(e.target.value)}
        />
        <button className="pill text-[11px]" onClick={() => onAdvanced(!advanced)}>
          {advanced ? "use the default location" : "choose a location"}
        </button>
      </div>

      {advanced && (
        <input
          className="inp mono w-full mt-2"
          placeholder={defaultParent}
          value={parent}
          spellCheck={false}
          onChange={(e) => onParent(e.target.value)}
        />
      )}

      <p className="mono text-[11px] text-[var(--haze)] mt-3 break-all">{resolved}</p>

      {bad && (
        <p className="text-[12px] mt-2" style={{ color: "var(--video)" }}>
          Letters, digits, hyphens and underscores only.
        </p>
      )}

      {embedder && (
        <div className="mt-6">
          <div className="eyebrow mb-2">embedder</div>
          <p className="text-[13px] leading-relaxed">
            <span className="mono" style={{
              color: embedder.available ? "var(--index)" : "var(--haze)",
            }}>
              {embedder.model ?? embedder.backend}
            </span>
            <span className="text-[var(--haze)]"> — {embedder.reason}</span>
          </p>
          {embedder.available && !embedder.sees_images && (
            <p className="text-[12px] leading-relaxed mt-2 flex gap-2 items-start"
               style={{ color: "var(--video)" }}>
              <Icon name="warning" size={14} className="mt-0.5 shrink-0" />
              This model cannot see images. Photographs would be embedded from their
              filenames and any text they carry — searchable, but not by what they
              look like.
            </p>
          )}
          {!embedder.available && (
            <p className="text-[12px] text-[var(--haze)] leading-relaxed mt-2">
              {embedder.setup_hint} The table will still be full-text searchable, and
              it cannot gain vectors later without being rebuilt.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
