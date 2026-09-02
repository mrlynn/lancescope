"use client";

/** What the scan found, and what this build would do with it.
 *
 *  Three separate claims share this screen and must not blur into "N files":
 *  what is here, what this build can decode, and what was left out of the counts.
 *  A missing ffmpeg is not an empty folder, and a video excluded by a checkbox is
 *  not a video this tool cannot read.
 */

import { Bytes, Caveat, Th, Td } from "@/app/components/console/atoms";
import Icon from "@/app/components/Icon";
import type { MediaKind, ScanResult } from "@/app/lib/ingest";

const KIND_LABEL: Record<MediaKind, string> = {
  image: "images",
  video: "video",
  audio: "audio",
  pdf: "PDF",
};

export function FoundSummary({
  scan, picked, onToggle,
}: {
  scan: ScanResult;
  picked: Set<MediaKind>;
  onToggle: (k: MediaKind) => void;
}) {
  if (scan.readable === null || scan.readable === false) {
    return (
      <div
        className="text-[13px] leading-relaxed px-3.5 py-3 rounded-sm"
        style={{
          background: "rgb(var(--video-rgb) / 0.12)",
          border: "1px solid rgb(var(--video-rgb) / 0.4)",
          color: "var(--video)",
        }}
      >
        {scan.note}
      </div>
    );
  }

  if (scan.found.length === 0) {
    return (
      <div className="text-[13px] text-[var(--haze)] leading-relaxed">
        {scan.note || "Nothing here that this tool can ingest."}
      </div>
    );
  }

  return (
    <div>
      <div className="eyebrow mb-3">found</div>
      <table className="w-full">
        <thead>
          <tr>
            <Th>include</Th>
            <Th>kind</Th>
            <Th right>files</Th>
            <Th right>size</Th>
            <Th>extensions</Th>
          </tr>
        </thead>
        <tbody>
          {scan.found.map((f) => {
            const ready = scan.readiness[f.kind];
            const usable = ready?.available !== false;
            return (
              <tr key={f.kind}>
                <Td>
                  <input
                    type="checkbox"
                    checked={picked.has(f.kind) && usable}
                    disabled={!usable}
                    onChange={() => onToggle(f.kind)}
                    aria-label={`include ${f.kind}`}
                  />
                </Td>
                <Td dim={!usable}>{KIND_LABEL[f.kind]}</Td>
                <Td right dim={!usable}>{f.files.toLocaleString()}</Td>
                <Td right><Bytes n={f.bytes} /></Td>
                <Td dim>{f.extensions.slice(0, 4).join(" ")}</Td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {scan.unsupported.length > 0 && (
        <details className="mt-6">
          <summary className="eyebrow cursor-pointer">
            not ingestable — {scan.unsupported.length} extension
            {scan.unsupported.length === 1 ? "" : "s"}
          </summary>
          <table className="w-full mt-3">
            <tbody>
              {scan.unsupported.slice(0, 8).map((u) => (
                <tr key={u.extension}>
                  <Td dim>{u.extension}</Td>
                  <Td right dim>{u.files.toLocaleString()}</Td>
                  <Td right><Bytes n={u.bytes} /></Td>
                  <Td dim className="truncate max-w-[18rem]">
                    {u.examples.slice(0, 2).join(", ")}
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}

      <p className="mono text-[11px] text-[var(--haze)] mt-5">
        {scan.total_files.toLocaleString()} files ·{" "}
        <Bytes n={scan.total_bytes} /> · scanned in {Math.round(scan.ms)} ms
        {scan.hidden_skipped > 0 && ` · ${scan.hidden_skipped.toLocaleString()} hidden skipped`}
      </p>

      {scan.warnings.map((w) => (
        <p
          key={w}
          className="text-[12px] leading-relaxed mt-3 flex gap-2 items-start"
          style={{ color: "var(--video)" }}
        >
          <Icon name="warning" size={14} className="mt-0.5 shrink-0" />
          {w}
        </p>
      ))}

      {scan.truncated && <Caveat>{scan.note}</Caveat>}
    </div>
  );
}
