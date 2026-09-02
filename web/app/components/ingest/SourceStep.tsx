"use client";

/** Choosing a source directory.
 *
 *  Paste a path and press Check — the same shape the settings page already teaches
 *  for adding a connection, because there is no file picker anywhere in this app
 *  and inventing one here would mean the two ways of naming a directory looked
 *  different for no reason the user can see.
 *
 *  `pickDirectory` is the seam for a native picker. It resolves `null` everywhere
 *  today, so the Browse button renders nowhere and the call site is already written
 *  for the day it does not.
 */

import { useState } from "react";

import Icon from "@/app/components/Icon";
import { pickDirectory, pickerAvailable } from "@/app/lib/native";

export function SourceStep({
  value, onChange, onCheck, busy, sources,
}: {
  value: string;
  onChange: (v: string) => void;
  onCheck: () => void;
  busy: boolean;
  sources: string[];
}) {
  const [picking, setPicking] = useState(false);

  return (
    <section>
      <div className="eyebrow mb-3">source directory</div>

      <div className="flex flex-wrap gap-2 items-center">
        <input
          className="inp mono flex-1 min-w-[18rem]"
          placeholder="/Users/you/Pictures"
          value={value}
          spellCheck={false}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && value.trim() && !busy) onCheck();
          }}
        />
        {pickerAvailable() && (
          <button
            className="btn"
            disabled={picking}
            onClick={async () => {
              setPicking(true);
              try {
                const picked = await pickDirectory();
                if (picked) onChange(picked);
              } finally {
                setPicking(false);
              }
            }}
          >
            Browse…
          </button>
        )}
        <button
          className="btn btn-accent"
          disabled={!value.trim() || busy}
          onClick={onCheck}
        >
          <Icon name={busy ? "clock" : "search"} size={14} />
          {busy ? "Looking…" : "Check"}
        </button>
      </div>

      <p className="text-[12px] text-[var(--haze)] leading-relaxed mt-3">
        Nothing is read. This walks directory entries and asks the filesystem for
        sizes, which is why it can survey a photo library in a couple of seconds.
      </p>

      {sources.length > 0 && (
        <div className="mt-5">
          <div className="eyebrow mb-2">recent</div>
          <div className="flex flex-wrap gap-2">
            {sources.map((s) => (
              <button
                key={s}
                className="pill mono text-[11px]"
                title={s}
                onClick={() => onChange(s)}
              >
                {s.replace(/^.*\/([^/]+\/[^/]+)$/, "…/$1")}
              </button>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
