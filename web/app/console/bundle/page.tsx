"use client";

/** Reading a bundle somebody else made.
 *
 *  The other half of the thing the export button starts. A markdown bundle is for a
 *  person and needs nothing to read it; this is for the JSON, and it exists so that a
 *  document produced against a database you cannot reach still renders as findings
 *  with their evidence rather than as a wall of braces.
 *
 *  Everything here reuses the components the live console renders with — the same
 *  `FindingCard`, the same evidence table — because the point of a bundle is that
 *  what the reader sees is what the author saw. A second set of components would
 *  drift, and the drift would be invisible to both of them.
 *
 *  Nothing is fetched. The file is read in the browser and never uploaded: a bundle
 *  is somebody else's database, and sending it to this server to be parsed would
 *  make a document about a private root travel further than the person who wrote it
 *  chose to send it.
 */

import Link from "next/link";
import { useCallback, useState } from "react";

import Icon from "@/app/components/Icon";
import AppBar from "@/app/components/nav/AppBar";
import { FindingCard, PartialAnalysis } from "@/app/components/console/Findings";
import { Empty } from "@/app/components/console/atoms";
import { fmtBytes } from "@/app/lib/api";
import type { Bundle } from "@/app/lib/catalog";
import { useSavedQueries } from "@/app/lib/queries";

/** A parse that failed and a file that is not a bundle are different problems and
 *  get different sentences — the first is a broken file, the second is the wrong
 *  file, and telling somebody "invalid JSON" about a CSV helps nobody. */
function read(text: string): { bundle: Bundle } | { error: string } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { error: "This is not JSON. If it is the markdown rendering, open it in "
                  + "any editor — it is written to be read as it is." };
  }
  const b = parsed as Partial<Bundle>;
  if (typeof b?.lancescope_bundle !== "number") {
    return { error: "This is JSON, but not a LanceScope bundle — it carries no "
                  + "`lancescope_bundle` version. A query result exported as JSON "
                  + "looks like this and is a different thing." };
  }
  return { bundle: b as Bundle };
}

export default function OpenBundle() {
  const [bundle, setBundle] = useState<Bundle | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const [imported, setImported] = useState(0);

  // Imported into whichever database this console is pointed at, which is the only
  // place they could go: saved queries are keyed by root, and a bundle's root is
  // redacted by design. Said out loud below rather than assumed.
  const { save } = useSavedQueries("imported");

  const take = useCallback(async (file: File) => {
    setError(null);
    setImported(0);
    const outcome = read(await file.text());
    if ("error" in outcome) {
      setBundle(null);
      setError(outcome.error);
    } else {
      setBundle(outcome.bundle);
    }
  }, []);

  return (
    <main className="mx-auto max-w-4xl px-6 py-10">
      <AppBar crumbs={[{ label: "Console", href: "/console" }, { label: "Open a bundle" }]} />

      {!bundle && (
        <p className="text-[14px] text-[var(--haze)] leading-relaxed max-w-2xl mb-8">
          Drop a bundle somebody sent you. It is read here in the browser and never
          uploaded — a bundle describes somebody else&apos;s database, and this one has
          no business seeing it.
        </p>
      )}

      <div
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          const file = e.dataTransfer.files[0];
          if (file) take(file);
        }}
        className="rounded-sm border border-dashed px-5 py-6 mb-6 flex flex-wrap
                   items-center gap-3"
        style={{ borderColor: over ? "var(--index)" : "var(--rule)",
                 background: over ? "rgb(var(--index-rgb) / 0.06)" : "transparent" }}
      >
        <Icon name="external" size={16} />
        <span className="text-[13px] text-[var(--body)]">
          {bundle ? "Drop another to replace it" : "Drop a .json bundle here"}
        </span>
        <label className="btn mono !h-[26px] !px-2.5 text-[10px] tracking-[0.14em]
                          uppercase cursor-pointer">
          choose a file
          <input type="file" accept="application/json,.json" className="hidden"
                 onChange={(e) => {
                   const file = e.target.files?.[0];
                   if (file) take(file);
                 }} />
        </label>
      </div>

      {error && (
        <div className="text-[12px] leading-relaxed px-3.5 py-3 rounded-sm mb-6"
             style={{ background: "rgb(var(--video-rgb) / 0.12)",
                      border: "1px solid rgb(var(--video-rgb) / 0.4)",
                      color: "var(--body)" }}>
          {error}
        </div>
      )}

      {!bundle && !error && (
        <Empty>
          Nothing open. Bundles come from the <Link href="/console"
            className="underline underline-offset-2">console</Link> — the insights tab
          and every query result offer one.
        </Empty>
      )}

      {bundle && (
        <>
          <Header b={bundle} />
          <Sections b={bundle} />
          {bundle.saved_queries?.length > 0 && (
            <section className="mt-8">
              <h2 className="eyebrow mb-2">saved queries</h2>
              <p className="text-[13px] text-[var(--haze)] leading-relaxed max-w-2xl mb-3">
                {bundle.saved_queries.length} question{bundle.saved_queries.length === 1
                  ? "" : "s"} the author had kept. Importing them puts them in this
                browser under a database called <span className="mono">imported</span>,
                because a bundle&apos;s own root is redacted and these would otherwise
                have nowhere to belong.
              </p>
              <button className="btn mono !h-[26px] !px-2.5 text-[10px] tracking-[0.14em]
                                 uppercase"
                      onClick={() => {
                        for (const q of bundle.saved_queries) {
                          save(q.name ?? `${bundle.table} ${q.spec.mode}`, q.table, q.spec);
                        }
                        setImported(bundle.saved_queries.length);
                      }}>
                <Icon name="check" size={12} />import
              </button>
              {imported > 0 && (
                <span className="mono text-[10px] text-[var(--haze)] ml-3">
                  {imported} imported — they appear in the query tab under{" "}
                  <span className="mono">imported</span>
                </span>
              )}
            </section>
          )}
        </>
      )}
    </main>
  );
}

function Header({ b }: { b: Bundle }) {
  const cost = fmtBytes(b.cost?.read_bytes ?? 0);
  return (
    <section className="mb-8">
      <h1 className="mono text-[18px] text-[var(--bright)]">{b.table}</h1>
      <p className="mono text-[11px] text-[var(--haze)] mt-1">
        {b.generated_by} · {b.generated_at} · assembling it read{" "}
        <span style={{ color: "var(--index)" }}>{cost.value} {cost.unit}</span> in{" "}
        {b.cost?.read_iops?.toLocaleString()} IOs
      </p>
      <p className="text-[13px] text-[var(--body)] leading-relaxed mt-3 max-w-2xl">
        {b.paths === "redacted"
          ? <>Paths were redacted by the author: <span className="mono">&lt;root&gt;</span>{" "}
             stands for a <span className="mono">{b.connection?.scheme}</span> root.</>
          : <>Paths were kept at the author&apos;s request.</>}
        {/* Only where there is a query. A bundle with none saying its rows are absent
            invents a result the author never ran. */}
        {b.query && <> The rows it returned are not in here — the reproduction below
          re-runs them against your own copy of the data.</>}
      </p>
      {b.incomplete?.length > 0 && (
        <div className="rounded-sm border px-4 py-3 mt-4"
             style={{ borderColor: "rgb(var(--video-rgb) / 0.4)",
                      background: "rgb(var(--video-rgb) / 0.06)" }}>
          <div className="flex items-center gap-2 mono text-[12px]"
               style={{ color: "var(--video)" }}>
            <Icon name="warning" size={14} />
            {b.incomplete.length} section{b.incomplete.length === 1 ? "" : "s"} could
            not be collected
          </div>
          <ul className="mt-2 space-y-1">
            {b.incomplete.map((n) => (
              <li key={n.section} className="mono text-[10px] text-[var(--haze)]">
                {n.section} — {n.error}: {n.message}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function Sections({ b }: { b: Bundle }) {
  const q = b.query;
  const weights = b.weights;
  const heaviest = weights?.columns?.[0];
  return (
    <>
      {b.schema && (
        <section className="mb-8">
          <h2 className="eyebrow mb-2">the table</h2>
          <p className="text-[13px] text-[var(--body)] leading-relaxed max-w-2xl">
            {b.schema.rows.toLocaleString()} rows at version {b.schema.version},
            storage format {b.schema.storage_version},{" "}
            {b.schema.stats.num_fragments.toLocaleString()} fragment
            {b.schema.stats.num_fragments === 1 ? "" : "s"}
            {b.schema.blob_columns?.length > 0 && <>, with{" "}
              <span className="mono">{b.schema.blob_columns.join(", ")}</span> in blob
              side files</>}.
          </p>
        </section>
      )}

      {b.findings && (
        <section className="mb-8">
          <h2 className="eyebrow mb-2">findings</h2>
          <PartialAnalysis d={b.findings} />
          <div className="space-y-3 mt-3">
            {b.findings.findings.map((f) => <FindingCard key={f.id} f={f} />)}
          </div>
          {b.findings.findings.length === 0 && (
            <Empty>Nothing fired on this table when the bundle was made.</Empty>
          )}
        </section>
      )}

      {q && (
        <section className="mb-8">
          <h2 className="eyebrow mb-2">the query</h2>
          <p className="mono text-[12px] text-[var(--body)]">
            {q.mode} · returned {q.returned.toLocaleString()} · {q.ms} ms ·{" "}
            <span style={{ color: "var(--index)" }}>
              {fmtBytes(q.read_bytes).value} {fmtBytes(q.read_bytes).unit}
            </span>{" "}
            · {q.read_iops} IOs
          </p>
          <ul className="mt-2 space-y-1">
            {q.plan?.paths?.map((p) => (
              <li key={p.name} className="text-[12px] text-[var(--haze)]">
                <span className="mono text-[var(--bright)]">{p.name}</span> — {p.meaning}
              </li>
            ))}
          </ul>
          <pre className="mono text-[11px] leading-relaxed mt-3 p-3 rounded-sm
                          overflow-x-auto"
               style={{ background: "rgb(var(--index-rgb) / 0.06)",
                        border: "1px solid var(--rule)" }}>
            {q.reproduction}
          </pre>
        </section>
      )}

      {weights && heaviest && (
        <section className="mb-8">
          <h2 className="eyebrow mb-2">what a full pass weighs</h2>
          <p className="text-[13px] text-[var(--body)] leading-relaxed max-w-2xl">
            {fmtBytes(weights.bytes).value} {fmtBytes(weights.bytes).unit} in the
            columns, {fmtBytes(weights.floor_bytes).value}{" "}
            {fmtBytes(weights.floor_bytes).unit} once the per-file floor is paid.
            Heaviest is <span className="mono">{heaviest.name}</span> at{" "}
            {fmtBytes(heaviest.bytes).value} {fmtBytes(heaviest.bytes).unit}. This is
            a property of the table, so it holds for any reader — not only the one
            that made this bundle.
          </p>
        </section>
      )}

      {b.environment && (
        <section className="mb-8">
          <h2 className="eyebrow mb-2">what read it</h2>
          <p className="mono text-[11px] text-[var(--haze)]">
            pylance {b.environment.versions?.lance} · pyarrow{" "}
            {b.environment.versions?.pyarrow} · python {b.environment.versions?.python}
            {" · "}root resolved from {b.connection?.provenance}
          </p>
        </section>
      )}
    </>
  );
}
