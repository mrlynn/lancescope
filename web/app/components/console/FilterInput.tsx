"use client";

/** A filter box that knows what is in the table.
 *
 *  The box this replaces was a text input with a placeholder. To use it you had to
 *  already know the column names, their types, and the values in them — which is
 *  everything the console had just finished telling you on the other tabs, and
 *  nothing it was willing to tell you here.
 *
 *  Three completions, decided from where the caret is:
 *
 *  **Columns**, at the start of an expression or after `and` / `or` / `(`.
 *  **Operators**, once a column is named — and only the ones its type accepts, so
 *  `LIKE` is never offered on an integer and a vector is only ever asked whether it
 *  is there.
 *  **Values**, after an operator on a column short enough to have a vocabulary.
 *  Those come from a sample, and the list says so rather than implying it is the
 *  whole column.
 *
 *  No request is made while typing. The table's columns and facets are read once
 *  when the workspace opens; everything below is local.
 */

import { useMemo, useRef, useState } from "react";
import type { CompletionColumn } from "@/app/lib/catalog";

/** Where the caret is, in terms of what would help. */
type Slot = "column" | "operator" | "value";

type Suggestion = {
  /** What is inserted. */
  insert: string;
  /** What is shown. Same as `insert` for a column or a value; an operator shows
   *  itself and its meaning is carried by `note`. */
  label: string;
  note: string;
  kind: string;
};

/** Anything that can end an expression, so the next word starts a fresh one. */
const JOINERS = /(?:\band\b|\bor\b|\bnot\b|\(|,)\s*$/i;

/** The operators a value can follow. `IS NULL` deliberately absent: it takes none. */
const VALUE_OPS = /(?:=|!=|<>|<=|>=|<|>|\bLIKE\b|\bIN\b)\s*$/i;

/** A word being typed: column names, keywords and bare numbers all look like this. */
const WORD = /[A-Za-z0-9_.]*$/;

/** True when the caret sits inside a single-quoted literal, which changes what the
 *  word being typed is — the quote is part of it, and the value list is quoted too. */
function inString(before: string): boolean {
  let open = false;
  for (let i = 0; i < before.length; i++) {
    if (before[i] !== "'") continue;
    // '' inside a literal is an escaped quote, not the end of one.
    if (open && before[i + 1] === "'") { i++; continue; }
    open = !open;
  }
  return open;
}

/** What the caret is asking for, and the word it has typed so far. */
export function readSlot(before: string, columns: CompletionColumn[]) {
  if (inString(before)) {
    const from = before.lastIndexOf("'");
    const head = before.slice(0, from).trimEnd();
    const col = head.match(/([A-Za-z0-9_.]+)\s*(?:=|!=|<>|<=|>=|<|>|\bLIKE\b|\bIN\b)\s*\(?\s*$/i);
    return { slot: "value" as Slot, word: before.slice(from), column: col?.[1] ?? null };
  }

  const word = before.match(WORD)?.[0] ?? "";
  const head = before.slice(0, before.length - word.length);
  const trimmed = head.trimEnd();

  if (VALUE_OPS.test(trimmed) || /(?:=|!=|<>|<=|>=|<|>)\s*\(?\s*$/.test(trimmed)) {
    const col = trimmed.match(/([A-Za-z0-9_.]+)\s*(?:=|!=|<>|<=|>=|<|>|\bLIKE\b|\bIN\b)\s*\(?\s*$/i);
    return { slot: "value" as Slot, word, column: col?.[1] ?? null };
  }

  // A column already named, with nothing but space after it, wants an operator.
  const named = trimmed.match(/([A-Za-z0-9_.]+)$/);
  if (word === "" && named && columns.some((c) => c.name === named[1])) {
    return { slot: "operator" as Slot, word, column: named[1] };
  }

  if (trimmed === "" || JOINERS.test(trimmed)) {
    return { slot: "column" as Slot, word, column: null };
  }

  // Mid-expression and not obviously anywhere: offering columns is the useful
  // guess, since `year = 2025 and tr…` is by far the common case.
  return { slot: "column" as Slot, word, column: null };
}

function suggestionsFor(
  slot: Slot,
  word: string,
  column: string | null,
  columns: CompletionColumn[],
): Suggestion[] {
  const needle = word.replace(/^'/, "").toLowerCase();
  const matches = (s: string) => s.replace(/^'/, "").toLowerCase().startsWith(needle);

  if (slot === "operator") {
    const col = columns.find((c) => c.name === column);
    return (col?.operators ?? []).map((op) => ({
      insert: op, label: op, kind: "operator",
      note: col && !col.filterable ? `${col.kind} — presence only` : col?.type ?? "",
    }));
  }

  if (slot === "value") {
    const col = columns.find((c) => c.name === column);
    if (!col || col.values.length === 0) return [];
    return col.values.filter(matches).map((v) => ({
      insert: v, label: v, kind: "value",
      note: col.values_complete
        ? "one of all values"
        : `seen in ${col.values_scanned.toLocaleString()} rows sampled`,
    }));
  }

  return columns
    .filter((c) => matches(c.name))
    .map((c) => ({
      insert: c.name,
      label: c.name,
      kind: c.kind,
      note: c.filterable ? c.type : `${c.type} — presence only`,
    }));
}

export function FilterInput({
  value, onChange, onEnter, columns, placeholder, className = "",
}: {
  value: string;
  onChange: (v: string) => void;
  onEnter?: () => void;
  columns: CompletionColumn[];
  placeholder?: string;
  className?: string;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const [caret, setCaret] = useState(0);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  // Set while an accepted completion is on its way into the box and cleared when
  // the caret lands after it. A Tab that arrives inside that window is swallowed
  // rather than acted on: the text has already changed and the caret has not, so
  // anything derived from the pair would splice at the wrong offset.
  const settling = useRef(false);

  const { slot, word, column } = useMemo(
    () => readSlot(value.slice(0, caret), columns),
    [value, caret, columns],
  );
  const items = useMemo(
    () => suggestionsFor(slot, word, column, columns).slice(0, 12),
    [slot, word, column, columns],
  );

  // The highlighted row is an index into a list that changes on every keystroke, so
  // leaving it where it was points it at something else. Reset in the events that
  // move the caret or the text rather than in an effect watching them: the reset
  // belongs to the keystroke, and an effect would be React re-deriving something it
  // was told directly.
  const sync = () => {
    setCaret(ref.current?.selectionStart ?? 0);
    setActive(0);
  };

  // The caret in state is a frame behind the one in the box: it is set in a rAF
  // after the event that moved it. Rendering can wait a frame; a keystroke cannot,
  // because a Tab arriving in the same frame as the character before it would
  // splice the completion in at the old offset. So the key handler reads the caret
  // off the DOM and derives its own list from it, and hands both to `accept`.
  const liveAt = (c: number) => {
    const s = readSlot(value.slice(0, c), columns);
    return { ...s, items: suggestionsFor(s.slot, s.word, s.column, columns).slice(0, 12) };
  };

  const accept = (s: Suggestion, ctx = { caret, word, slot }) => {
    const before = value.slice(0, ctx.caret);
    const head = before.slice(0, before.length - ctx.word.length);
    // An operator wants a space in front of it when the column is flush against
    // the caret, and everything wants one after it.
    const glue = ctx.slot === "operator" && head.length > 0 && !/\s$/.test(head) ? " " : "";
    const next = `${head}${glue}${s.insert} ${value.slice(ctx.caret)}`;
    const at = head.length + glue.length + s.insert.length + 1;
    onChange(next);
    // Left open after a column or an operator, because the caret lands in the next
    // slot and a predicate is finished by pressing Tab three times without ever
    // leaving the box; closing here made the second Tab a focus change instead.
    // Closed after a value: the predicate is complete, and the next Enter belongs
    // to running it rather than to a list of columns nobody asked to see again.
    setOpen(ctx.slot !== "value");
    settling.current = true;
    requestAnimationFrame(() => {
      ref.current?.setSelectionRange(at, at);
      setCaret(at);
      setActive(0);
      settling.current = false;
    });
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (settling.current && (e.key === "Tab" || e.key === "Enter")) {
      e.preventDefault();
      return;
    }
    const at = ref.current?.selectionStart ?? caret;
    const live = at === caret
      ? { slot, word, column, items }
      : liveAt(at);
    const showing = open && live.items.length > 0;
    if (showing && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault();
      const n = live.items.length;
      setActive((i) => (i + (e.key === "ArrowDown" ? 1 : n - 1)) % n);
      return;
    }
    if (showing && (e.key === "Tab" || e.key === "Enter")) {
      // Enter accepts the highlighted suggestion rather than running the query.
      // Running with a half-typed column name is never what was meant.
      e.preventDefault();
      const i = Math.min(active, live.items.length - 1);
      accept(live.items[i], { caret: at, word: live.word, slot: live.slot });
      return;
    }
    if (e.key === "Escape") { setOpen(false); return; }
    if (e.key === "Enter") { onEnter?.(); return; }
    // Any other key moves the caret; reading it after the event is what makes the
    // slot follow the cursor rather than lag one keystroke behind.
    requestAnimationFrame(sync);
  };

  return (
    <div className="relative flex-1">
      <input
        ref={ref}
        className={`qin mono w-full ${className}`}
        value={value}
        placeholder={placeholder}
        spellCheck={false}
        autoComplete="off"
        onChange={(e) => { onChange(e.target.value); setOpen(true); requestAnimationFrame(sync); }}
        onKeyDown={onKeyDown}
        onClick={sync}
        onFocus={() => { setOpen(true); sync(); }}
        // A click on a suggestion blurs the input first, so the list has to outlive
        // the blur long enough for the click to land on it.
        onBlur={() => setTimeout(() => setOpen(false), 120)}
      />

      {open && items.length > 0 && (
        <ul
          className="absolute z-30 left-0 right-0 top-full mt-1 max-h-[260px] overflow-y-auto
                     rounded-sm border border-[var(--rule)] bg-[var(--ink-2)] shadow-lg py-1"
          role="listbox"
        >
          {items.map((s, i) => (
            <li key={`${s.kind}:${s.insert}`}>
              <button
                type="button"
                role="option"
                aria-selected={i === active}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => accept(s)}
                onMouseEnter={() => setActive(i)}
                className={`w-full text-left px-3 py-1.5 flex items-baseline gap-3
                            ${i === active ? "bg-[var(--ink-3)]" : ""}`}
              >
                <span className="mono text-[12px] text-[var(--bright)] truncate">{s.label}</span>
                <span className="mono text-[10px] text-[var(--dim)] ml-auto shrink-0">
                  {s.note}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
