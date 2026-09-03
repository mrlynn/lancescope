"use client";

import { useEffect, useState } from "react";
import Icon from "@/app/components/Icon";
import { fmtBytes } from "@/app/lib/api";

type Field = { name: string; type: string; blob: boolean };
type Schema = {
  moments: { rows: number; fields: Field[] };
  segments: { rows: number; fields: Field[] };
  on_disk: { blob_bytes: number; meta_bytes: number; ratio: number };
  storage_version: string;
};

function Table({ name, rows, fields, note }: {
  name: string; rows: number; fields: Field[]; note: string;
}) {
  return (
    <div className="panel p-6">
      <div className="flex items-baseline justify-between mb-1">
        <span className="mono text-[15px] text-[var(--bright)]">{name}</span>
        <span className="eyebrow">{rows.toLocaleString()} rows</span>
      </div>
      <p className="text-[13px] text-[var(--haze)] mb-5 leading-relaxed">{note}</p>
      <div className="space-y-1.5">
        {fields.map((f) => (
          <div
            key={f.name}
            className="flex items-baseline gap-3 mono text-[12px] px-2.5 py-1.5 rounded-sm"
            style={
              f.blob
                ? { background: "rgb(var(--video-rgb) / 0.12)", border: "1px solid rgb(var(--video-rgb) / 0.4)" }
                : undefined
            }
          >
            <span
              className="w-[168px] shrink-0"
              style={{ color: f.blob ? "var(--video)" : "var(--body)" }}
            >
              {f.name}
            </span>
            <span className="text-[var(--haze)] truncate">{f.type}</span>
            {f.blob && (
              <span className="ml-auto text-[10px] shrink-0" style={{ color: "var(--video)" }}>
                BLOB V2
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function SchemaView({ onClose }: { onClose: () => void }) {
  const [data, setData] = useState<Schema | null>(null);

  useEffect(() => {
    fetch("/api/schema").then((r) => r.json()).then(setData).catch(() => {});
    const onKey = (e: KeyboardEvent) => {
      // A synthetic event can arrive without one; reading it would throw.
      if (!e.key) return;
      if (e.key === "Escape" || e.key.toLowerCase() === "s") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!data) return null;

  const blob = fmtBytes(data.on_disk.blob_bytes);
  const meta = fmtBytes(data.on_disk.meta_bytes);
  const metaPct = (data.on_disk.meta_bytes /
    Math.max(data.on_disk.blob_bytes + data.on_disk.meta_bytes, 1)) * 100;

  return (
    <div
      // pb clears the byte rail, which stays visible on top of this view.
      className="fixed inset-0 z-[58] overflow-y-auto px-[var(--stage-pad)] pt-10 pb-[250px]"
      style={{ background: "var(--scrim)" }}
      onClick={onClose}
    >
      <div className="max-w-[1180px] mx-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-baseline justify-between mb-7">
          <div>
            <h2 className="text-[24px] font-bold text-[var(--bright)] tracking-tight">
              Two tables. One format. No services.
            </h2>
            <p className="eyebrow normal-case mt-1.5">
              Lance storage format {data.storage_version} &middot; read live off disk
            </p>
          </div>
          <button
            onClick={onClose}
            className="iconbtn shrink-0"
            data-tip="Close — S"
            data-tip-side="left"
            aria-label="Close"
          >
            <Icon name="close" size={16} />
          </button>
        </div>

        <div className="grid gap-5 md:grid-cols-2 mb-7">
          <Table
            name="moments" rows={data.moments.rows} fields={data.moments.fields}
            note="One row per keyframe. This is everything search touches."
          />
          <Table
            name="segments" rows={data.segments.rows} fields={data.segments.fields}
            note="One row per playable MP4 chunk. The video is a column."
          />
        </div>

        {/* The file split — the reason searching can't read video even by accident. */}
        <div className="panel p-6">
          <div className="eyebrow mb-4">What that means on disk</div>
          <div className="flex h-9 rounded-sm overflow-hidden border border-[var(--rule)]">
            <div
              className="grid place-items-center"
              style={{ width: `${Math.max(metaPct, 0.4)}%`, background: "var(--index)" }}
            />
            <div
              className="flex-1 grid place-items-center"
              style={{ background: "rgb(var(--video-rgb) / 0.22)" }}
            >
              <span className="mono text-[11px]" style={{ color: "var(--video)" }}>
                {blob.value} {blob.unit} of video, in .blob side files
              </span>
            </div>
          </div>
          <p className="text-[13px] text-[var(--haze)] mt-4 leading-relaxed max-w-3xl">
            Everything a search reads &mdash; embeddings, transcripts, thumbnails, all the
            metadata &mdash; is the{" "}
            <span style={{ color: "var(--index)" }}>{meta.value} {meta.unit}</span> on the
            left. The video sits in separate files that a scan never opens. It is not that
            we are careful not to read it; the bytes are not in the file being read.
          </p>
        </div>
      </div>
    </div>
  );
}
