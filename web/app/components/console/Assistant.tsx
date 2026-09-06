"use client";

/** Ask a question, watch which tools answered it, and see what both cost.
 *
 *  Every other panel in this console asks the table one question the console
 *  composed. This one hands the model the read surface and lets it decide — which is
 *  a different kind of thing to put in front of somebody, and the trace is why it is
 *  acceptable. A tool call that is not shown is a claim you cannot check, so the
 *  trace lists the arguments as well as the names: whether the model asked the
 *  question it then reported the answer to is the whole point of showing it.
 *
 *  Two meters, always. Tokens and dollars, because a console that makes read cost
 *  visible has no business hiding inference cost — and bytes, because "this answer
 *  cost 8 KB against a table holding 2.65 GB" is the most interesting sentence this
 *  product has, and it is never truer than when a model went and found it. */

import { useCallback, useState } from "react";
import Icon from "@/app/components/Icon";
import { Caveat, Cost, Empty, Eyebrow } from "@/app/components/console/atoms";
import { fmtBytes } from "@/app/lib/api";
import type { Capabilities } from "@/app/lib/settings";
import { type Answer, STOP_COPY, ask } from "@/app/lib/ops";

const EXAMPLES = [
  "Why would a search on this table be slow?",
  "Is this ready to train on?",
  "What would it cost to scan every column?",
  "What should I do about this table, and what would it cost?",
];

export function Assistant({ table, ai }: { table: string | null; ai: Capabilities | null }) {
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState<Answer | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  const run = useCallback(async (q: string) => {
    if (!q.trim() || busy) return;
    setBusy(true);
    setOut(null);
    setFailed(null);
    try {
      setOut(await ask(q, table));
    } catch (e) {
      setFailed(e instanceof Error ? e.message : "the request failed");
    } finally {
      setBusy(false);
    }
  }, [table, busy]);

  // Three states, not two — the third is the one that was wrong first. `ai` is null
  // while the capability probe is in flight, and treating that as "unavailable" put
  // "the assistant needs a model" on screen for a second on every load, including
  // for people whose model was configured and working. A sentence that is false while
  // it is showing is worse than a spinner.
  if (ai === null) return <Empty>checking what this console can ask…</Empty>;

  // The other two absences need different sentences. No provider at all is the
  // ordinary state on a fresh install; a provider whose model cannot hold a tool loop
  // is a working setup pointed at the wrong model, and telling somebody to "configure
  // intelligence" when they already have would be the unhelpful answer.
  if (!ai.available) {
    return (
      <Empty>
        The assistant needs a model. Settings &rsaquo; Intelligence — a local one
        through Ollama costs nothing, or a key gets the strongest results. Everything
        else in this console works without one, including the findings the assistant
        would be reading out.
      </Empty>
    );
  }
  if (!ai.tools_capable) {
    return (
      <Empty>
        {ai.models_by_role.deep.id || "This model"} answers questions but cannot hold
        a tool loop, so
        it has no way to go and look. Pick a model that supports tool calling in
        Settings &rsaquo; Intelligence. The Query panel&rsquo;s ask box still works —
        it needs one answer, not eight.
      </Empty>
    );
  }

  return (
    <>
      <div className="flex gap-2 mb-3">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); run(question); } }}
          placeholder={table ? `Ask about ${table}…` : "Ask about this database…"}
          className="flex-1 bg-[var(--ink-3)] border border-[var(--rule)] rounded-sm
                     px-3 py-2 text-[13px] text-[var(--body)] outline-none
                     focus:border-[var(--haze)]"
        />
        <button
          onClick={() => run(question)}
          disabled={busy || !question.trim()}
          className="mono text-[11px] px-3 py-2 rounded-sm border border-[var(--rule)]
                     text-[var(--haze)] disabled:opacity-40 hover:text-[var(--bright)]"
        >
          {busy ? "looking…" : "ask"}
        </button>
      </div>

      {!out && !busy && (
        <div className="flex flex-wrap gap-2 mb-5">
          {EXAMPLES.map((e) => (
            <button
              key={e}
              onClick={() => { setQuestion(e); run(e); }}
              className="text-[11px] px-2.5 py-1.5 rounded-sm border border-[var(--rule)]
                         text-[var(--haze)] hover:text-[var(--bright)] text-left"
            >
              {e}
            </button>
          ))}
        </div>
      )}

      <p className="text-[11px] text-[var(--dim)] mb-5 leading-relaxed">
        It reads metadata through the same routes this console does, and it cannot
        change anything. Asked to fix something it will plan it, and the plan is
        waiting under Plans — running it stays yours.
      </p>

      {failed && <Caveat>{failed}</Caveat>}
      {busy && <Empty>reading the table…</Empty>}
      {out && <Result out={out} />}
    </>
  );
}

function Result({ out }: { out: Answer }) {
  const [trace, setTrace] = useState(true);

  if (out.error) {
    return (
      <Empty>
        {out.error}
        {out.setup_hint ? ` ${out.setup_hint}` : ""}
      </Empty>
    );
  }

  return (
    <>
      {/* Said before the answer, not after. A partial answer that announces itself
          underneath has already been read as a whole one. */}
      {!out.complete && (
        <Caveat>
          {STOP_COPY[out.stop]} {out.detail}
        </Caveat>
      )}

      <div className="text-[13px] text-[var(--body)] leading-relaxed mb-5">
        <Prose text={out.answer} />
      </div>

      <div className="flex items-center justify-between mb-2">
        <button
          onClick={() => setTrace(!trace)}
          className="eyebrow flex items-center gap-1.5 hover:text-[var(--bright)]"
        >
          <Icon name={trace ? "check" : "plus"} size={11} />
          {out.trace.length} tool call{out.trace.length === 1 ? "" : "s"}
          {" · "}{out.turns} turn{out.turns === 1 ? "" : "s"}
        </button>
        <Cost bytes={out.read_bytes} iops={out.read_iops} label="finding this read" />
      </div>

      {trace && (
        <div className="space-y-1 mb-5">
          {out.trace.length === 0 ? (
            <p className="text-[12px] text-[var(--dim)]">
              It answered without looking anything up.
            </p>
          ) : out.trace.map((s, i) => (
            <div
              key={`${s.tool}-${i}`}
              className="mono text-[11px] flex items-baseline gap-2.5 px-2.5 py-1.5 rounded-sm
                         border border-[var(--hairline)]"
            >
              <span className="text-[var(--dim)] w-4 shrink-0">{i + 1}</span>
              <span className="text-[var(--bright)] shrink-0">{s.tool}</span>
              <span className="text-[var(--haze)] truncate">
                {Object.entries(s.arguments)
                  .map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(" ")}
              </span>
              <span className="ml-auto shrink-0 text-[var(--dim)]">
                {s.error ? (
                  <span style={{ color: "var(--video)" }}>{s.error}</span>
                ) : (
                  <>
                    {fmtBytes(s.read_bytes).value} {fmtBytes(s.read_bytes).unit} · {s.ms} ms
                  </>
                )}
              </span>
            </div>
          ))}
        </div>
      )}

      <Eyebrow>What asking cost</Eyebrow>
      <div className="mono text-[11px] text-[var(--haze)] flex flex-wrap gap-x-5 gap-y-1">
        <span>
          {out.usage.input_tokens.toLocaleString()} in ·{" "}
          {out.usage.output_tokens.toLocaleString()} out
        </span>
        <span>
          {out.cost_usd === null
            // A model nobody ships a price for. Said rather than shown as $0.00,
            // which would be the one number on this screen nobody could check.
            ? <span className="text-[var(--dim)]">unpriced model</span>
            : `$${out.cost_usd.toFixed(4)}`}
        </span>
        <span className="text-[var(--dim)]">{out.model}</span>
        <span className="text-[var(--dim)]">{(out.ms / 1000).toFixed(1)}s</span>
      </div>
    </>
  );
}


/** The little of Markdown a model actually uses in an answer, as React elements.
 *
 *  Not `marked`, and not `dangerouslySetInnerHTML`. This text was written by a model
 *  that has just been reading somebody's database — table names, column names, error
 *  strings — and the whole point of the envelope in `intel/tasks.py` is that such
 *  content is data. Handing it to an HTML parser at the last step would give back, in
 *  the browser, exactly the authority the prompt layer spends three paragraphs
 *  refusing it. React escapes text nodes; that is the property this wants.
 *
 *  Paragraphs, bullets, `**bold**` and `` `code` ``. A model that reaches for a table
 *  gets its pipes rendered literally, which is ugly and honest, and nobody has been
 *  asking for tables here. */
function Prose({ text }: { text: string }) {
  const blocks: React.ReactNode[] = [];
  let bullets: string[] = [];

  const flush = (key: string) => {
    if (bullets.length === 0) return;
    const items = bullets;
    bullets = [];
    blocks.push(
      <ul key={key} className="list-disc pl-5 mb-3 space-y-1.5">
        {items.map((b, i) => <li key={i}><Inline text={b} /></li>)}
      </ul>,
    );
  };

  text.split("\n").forEach((raw, i) => {
    const line = raw.trimEnd();
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    if (bullet) { bullets.push(bullet[1]); return; }
    flush(`ul-${i}`);
    if (!line.trim()) return;
    const heading = /^#{1,6}\s+(.*)$/.exec(line);
    if (heading) {
      blocks.push(
        <p key={i} className="eyebrow mt-4 mb-2">{heading[1]}</p>,
      );
      return;
    }
    blocks.push(<p key={i} className="mb-3"><Inline text={line} /></p>);
  });
  flush("ul-end");

  return <>{blocks}</>;
}

/** `**bold**` and `` `code` `` inside one line. Split rather than replaced, so every
 *  fragment stays a text node. */
function Inline({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
          return <strong key={i} className="text-[var(--bright)]">{part.slice(2, -2)}</strong>;
        }
        if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
          return (
            <code key={i} className="mono text-[12px] px-1 py-[1px] rounded-sm
                                     bg-[var(--ink-3)] text-[var(--bright)]">
              {part.slice(1, -1)}
            </code>
          );
        }
        return <span key={i}>{part}</span>;
      })}
    </>
  );
}
