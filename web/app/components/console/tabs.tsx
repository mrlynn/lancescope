"use client";

import { Bytes, Caveat, Cost, Empty, Eyebrow, Td, Th, fmtWhen } from "@/app/components/console/atoms";
import { fmtBytes } from "@/app/lib/api";
import type {
  Fragments, Indices, TableDetail, Versions,
} from "@/app/lib/catalog";

// ------------------------------------------------------------------- schema

export function SchemaTab({ d }: { d: TableDetail }) {
  // The split comes from walking the directory, and a remote root has none to walk.
  // The server sends null and a reason rather than zeros, because rendering those as
  // "0 B of ordinary Lance files" states a measurement that was never taken — on a
  // console whose whole claim is honest byte accounting, that is the one thing it
  // must not do.
  const measured = d.on_disk !== null;
  const { blob_bytes, meta_bytes, ratio, files } =
    d.on_disk ?? { blob_bytes: 0, meta_bytes: 0, ratio: 0, files: 0 };
  const total = Math.max(blob_bytes + meta_bytes, 1);
  const metaPct = (meta_bytes / total) * 100;

  return (
    <>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <Stat label="rows" value={d.rows.toLocaleString()} />
        <Stat label="columns" value={String(d.fields.length)} />
        <Stat label="version" value={`${d.version} of ${d.latest_version}`} />
        <Stat label="storage format" value={d.storage_version} />
      </div>

      <Eyebrow>Fields</Eyebrow>
      <div className="space-y-1.5 mb-7">
        {d.fields.map((f) => (
          <div
            key={f.name}
            className="flex items-baseline gap-3 mono text-[12px] px-2.5 py-1.5 rounded-sm"
            style={
              f.blob
                ? { background: "rgb(var(--video-rgb) / 0.12)", border: "1px solid rgb(var(--video-rgb) / 0.4)" }
                : { border: "1px solid transparent" }
            }
          >
            <span
              className="w-[180px] shrink-0"
              style={{ color: f.blob ? "var(--video)" : "var(--body)" }}
            >
              {f.name}
            </span>
            <span className="text-[var(--haze)] truncate">{f.type}</span>
            {!f.nullable && <span className="text-[10px] text-[var(--dim)]">NOT NULL</span>}
            {f.blob && (
              <span className="ml-auto text-[10px] shrink-0" style={{ color: "var(--video)" }}>
                BLOB — SIDE FILE
              </span>
            )}
          </div>
        ))}
      </div>

      <Eyebrow>{measured ? `On disk — ${files.toLocaleString()} files` : "On disk"}</Eyebrow>
      {!measured ? (
        <p className="text-[12px] text-[var(--haze)] leading-relaxed">
          Not measured. {d.on_disk_note} Everything above was read from the table
          itself, and the byte costs are real; only this panel is missing.
        </p>
      ) : blob_bytes > 0 ? (
        <>
          <div className="flex h-9 rounded-sm overflow-hidden border border-[var(--rule)]">
            <div style={{ width: `${Math.max(metaPct, 0.4)}%`, background: "var(--index)" }} />
            <div className="flex-1 grid place-items-center"
                 style={{ background: "rgb(var(--video-rgb) / 0.14)" }}>
              <span className="mono text-[11px]" style={{ color: "var(--video)" }}>
                {fmtBytes(blob_bytes).value} {fmtBytes(blob_bytes).unit} in .blob side files
              </span>
            </div>
          </div>
          <p className="text-[12px] text-[var(--haze)] mt-3 leading-relaxed">
            <Bytes n={meta_bytes} tone="index" /> of everything a scan reads, against{" "}
            <Bytes n={blob_bytes} tone="video" /> a scan never opens — {ratio.toLocaleString()} to 1.
          </p>
        </>
      ) : (
        <p className="text-[12px] text-[var(--haze)] leading-relaxed">
          No blob columns. Everything this table holds is{" "}
          <Bytes n={meta_bytes} tone="index" /> of ordinary Lance files.
        </p>
      )}
    </>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="panel px-4 py-3">
      <div className="eyebrow mb-1">{label}</div>
      <div className="mono text-[16px] text-[var(--bright)]">{value}</div>
    </div>
  );
}

// ----------------------------------------------------------------- versions

function Delta({ n }: { n: number }) {
  if (n === 0) return <span className="text-[var(--dim)]">—</span>;
  return (
    <span style={{ color: n > 0 ? "var(--index)" : "var(--video)" }}>
      {n > 0 ? "+" : ""}{n.toLocaleString()}
    </span>
  );
}

export function VersionsTab({ d }: { d: Versions }) {
  const refs = Object.keys(d.tags).length + Object.keys(d.branches).length;
  return (
    <>
      <p className="text-[12px] text-[var(--haze)] mb-5 leading-relaxed">
        {d.versions.length} version{d.versions.length === 1 ? "" : "s"}, newest first.
        {refs === 0 && " No tags or branches."}
      </p>
      <table className="w-full">
        <thead>
          <tr>
            <Th>ver</Th><Th>operation</Th><Th>when</Th>
            <Th right>rows</Th><Th right>frags</Th><Th right>files</Th><Th right>manifest</Th>
          </tr>
        </thead>
        <tbody>
          {d.versions.map((v) => (
            <tr key={v.version} style={{ borderBottom: "1px solid var(--hairline)" }}>
              <Td>
                <span style={{ color: v.version === d.current_version ? "var(--video)" : undefined }}>
                  {v.version}
                </span>
              </Td>
              <Td dim={!v.operation}>{v.operation ?? "unknown"}</Td>
              <Td dim>{fmtWhen(v.timestamp)}</Td>
              <Td right>
                {v.rows.toLocaleString()}
                {v.diff && <span className="ml-2 text-[11px]"><Delta n={v.diff.rows} /></span>}
              </Td>
              <Td right>
                {v.fragments}
                {v.diff && <span className="ml-2 text-[11px]"><Delta n={v.diff.fragments} /></span>}
              </Td>
              <Td right>{v.data_files}</Td>
              <Td right>
                <Bytes n={v.manifest_bytes} />
                {v.diff && (
                  <span className="ml-2 text-[11px]"><Delta n={v.diff.manifest_bytes} /></span>
                )}
              </Td>
            </tr>
          ))}
        </tbody>
      </table>
      {d.versions.some((v) => v.diff && Object.values(v.diff).every((n) => n === 0)) && (
        <Caveat>
          A version whose row, fragment and byte counts are all unchanged did something
          that doesn&rsquo;t move them — building an index, for instance. The operation
          column is the only thing that tells you which.
        </Caveat>
      )}
    </>
  );
}

// ------------------------------------------------------------------ indices

export function IndicesTab({ d }: { d: Indices }) {
  const bare = d.unindexed_vector_columns;
  return (
    <>
      {d.indices.length === 0 ? (
        <Empty>No indices on this table. Every query is a scan.</Empty>
      ) : (
        <div className="space-y-3 mb-6">
          {d.indices.map((i) => (
            <div key={i.name} className="panel p-4">
              <div className="flex items-baseline justify-between mb-2">
                <span className="mono text-[13px] text-[var(--bright)]">{i.name}</span>
                <span className="eyebrow">{i.type}</span>
              </div>
              <div className="mono text-[12px] text-[var(--haze)] space-y-1">
                <div>on {i.columns.join(", ") || "—"}</div>
                <div>
                  covering {i.fragment_ids.length} fragment
                  {i.fragment_ids.length === 1 ? "" : "s"}
                  {i.indexed_rows !== null && (
                    <> · {i.indexed_rows.toLocaleString()} rows indexed</>
                  )}
                  {i.unindexed_rows ? (
                    <span style={{ color: "var(--video)" }}>
                      {" "}· {i.unindexed_rows.toLocaleString()} rows NOT indexed
                    </span>
                  ) : null}
                </div>
              </div>

            </div>
          ))}
        </div>
      )}

      <Eyebrow>Columns with no index</Eyebrow>
      <div className="flex flex-wrap gap-2 mb-4">
        {d.unindexed_columns.map((c) => {
          const notable = bare.includes(c.name);
          return (
            <span
              key={c.name}
              title={c.type}
              className="mono text-[11px] px-2.5 py-1.5 rounded-sm border"
              style={
                notable
                  ? { borderColor: "var(--video)", color: "var(--video)",
                      background: "rgb(var(--video-rgb) / 0.09)" }
                  : { borderColor: "var(--rule)",
                      color: c.indexable ? "var(--haze)" : "var(--dim)" }
              }
            >
              {c.name}
              {c.vector_dim ? <span className="ml-1.5">[{c.vector_dim}]</span> : null}
              {!c.indexable && <span className="ml-1.5 text-[10px]">blob</span>}
            </span>
          );
        })}
      </div>

      {/* The unindexed-vector caveat that used to live here is now a finding —
          same claim, computed rather than written, and rendered under this panel by
          `PanelFindings`. Two voices saying it was one voice too many. */}
    </>
  );
}

// ---------------------------------------------------------------- fragments

export function FragmentsTab({ d }: { d: Fragments }) {
  return (
    <>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <Stat label="fragments" value={String(d.stats.num_fragments)} />
        <Stat label="rows" value={d.rows.toLocaleString()} />
        <Stat label="deleted rows" value={d.stats.num_deleted_rows.toLocaleString()} />
        <Stat label="small files" value={String(d.stats.num_small_files)} />
      </div>

      <table className="w-full">
        <thead>
          <tr>
            <Th>id</Th><Th right>rows</Th><Th right>deleted</Th>
            <Th right>data</Th>
            {d.has_blob_columns && <><Th right>blob</Th><Th right>blobs</Th></>}
            <Th>file</Th>
          </tr>
        </thead>
        <tbody>
          {d.fragments.map((f) => (
            <tr key={f.id} style={{ borderBottom: "1px solid var(--hairline)" }}>
              <Td>{f.id}</Td>
              <Td right>{f.rows.toLocaleString()}</Td>
              <Td right dim={f.deleted_rows === 0}>{f.deleted_rows}</Td>
              <Td right><Bytes n={f.data_bytes} tone="index" /></Td>
              {d.has_blob_columns && (
                <>
                  <Td right><Bytes n={f.blob_bytes} tone="video" /></Td>
                  <Td right dim>{f.blob_files}</Td>
                </>
              )}
              <Td dim className="truncate max-w-[180px]">
                {f.data_files[0]?.path.slice(0, 12)}… v{f.data_files[0]?.file_version}
              </Td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* The small-file caveat is a finding now — the rule that reports the count
          is the same rule that says why acting on it would be wrong. */}
    </>
  );
}

export { Cost };
