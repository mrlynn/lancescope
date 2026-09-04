"use client";

/** English to a predicate.
 *
 *  This used to live on a Rows panel of its own, above a plain text input, while
 *  the Query panel had completions and no way to ask a question in words. They
 *  were the same box twice, each missing the other's half. It draws nothing of its
 *  own now: it hands a draft to whatever box it is mounted above. */

import { useState } from "react";
import Icon from "@/app/components/Icon";
import { fmtBytes } from "@/app/lib/api";
import { type FilterDraft, askForFilter } from "@/app/lib/settings";

/** Ask in English, get a predicate to read before you run it.
 *
 *  Three things make this a draft rather than an answer. It lands in the filter box
 *  instead of being applied. It is dry-run counted, so "matches 99 of 1,114" tells
 *  you whether it understood the question before you spend a page read on it. And a
 *  refusal is a first-class outcome — a model that says it cannot express something
 *  is more useful than one that produces a filter that runs and means something
 *  else. */
export function AskForFilter({ table, model, example, onDraft }: {
  table: string;
  model: string;
  /** Written against this table's own columns. The hint used to name the demo
   *  corpus, which on anything else is a suggestion guaranteed to fail. */
  example: string;
  onDraft: (filter: string) => void;
}) {
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<FilterDraft | null>(null);

  const ask = async () => {
    if (!question.trim()) return;
    setBusy(true);
    setResult(null);
    try {
      const r = await askForFilter(table, question);
      setResult(r);
      if (r.filter) onDraft(r.filter);
    } catch (e) {
      setResult({ ok: false, error: e instanceof Error ? e.message : "ask failed" });
    } finally {
      setBusy(false);
    }
  };

  const tone = result?.valid ? "index" : "video";

  return (
    <div className="mb-4">
      <div className="flex gap-2">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); ask(); } }}
          placeholder={`Ask in English — rows where ${example}`}
          className="flex-1 bg-[var(--ink-3)] border border-[var(--rule)] rounded-sm
                     px-3 py-2 text-[12px] text-[var(--bright)] outline-none
                     focus:border-[var(--index)] transition-colors
                     placeholder:text-[var(--dim)]"
        />
        <button type="button" onClick={ask} disabled={busy || !question.trim()}
                className="btn mono text-[10px] tracking-[0.14em] uppercase">
          <Icon name="spark" size={14} />
          {busy ? "asking…" : "Translate"}
        </button>
      </div>

      {busy && (
        <p className="mono text-[10px] text-[var(--haze)] mt-2">
          {model} is writing a predicate. It lands in the filter box below for you to
          read — nothing runs until you say so.
        </p>
      )}

      {result && (
        <div className="mt-2 px-3.5 py-3 rounded-sm border"
             style={{ borderColor: `rgb(var(--${tone}-rgb) / 0.4)`,
                      background: `rgb(var(--${tone}-rgb) / 0.06)` }}>
          {result.valid ? (
            <div className="mono text-[12px]" style={{ color: "var(--index)" }}>
              matches {result.matched_rows?.toLocaleString()} of{" "}
              {result.total_rows?.toLocaleString()} rows
            </div>
          ) : (
            <div className="mono flex items-center gap-2 text-[12px]"
                 style={{ color: "var(--video)" }}>
              <Icon name="warning" size={14} />
              {result.confidence === "refuse"
                ? "this cannot be asked of these columns"
                : (result.error ?? "no filter produced")}
            </div>
          )}

          {result.explanation && (
            <p className="text-[12px] text-[var(--body)] leading-relaxed mt-1.5">
              {result.explanation}
            </p>
          )}
          {result.setup_hint && (
            <p className="text-[12px] text-[var(--haze)] leading-relaxed mt-1.5">
              {result.setup_hint}
            </p>
          )}

          {/* Both costs, because both were spent: bytes off the disk to describe the
              table, and tokens to write the sentence. */}
          <div className="mono text-[10px] text-[var(--haze)] mt-2">
            {result.model} · {((result.ms ?? 0) / 1000).toFixed(1)}s ·{" "}
            {result.cost_usd === 0
              ? "no cost, ran locally"
              : result.cost_usd == null ? "cost unknown" : `$${result.cost_usd.toFixed(5)}`}
            {result.context_read_bytes != null && (
              <> · {fmtBytes(result.context_read_bytes).value}{" "}
                {fmtBytes(result.context_read_bytes).unit} read to describe the table</>
            )}
            {result.values_included && result.faceted_columns?.length ? (
              <> · sent values of {result.faceted_columns.join(", ")}</>
            ) : result.values_included === false ? (
              <> · schema only, no row values sent</>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}
