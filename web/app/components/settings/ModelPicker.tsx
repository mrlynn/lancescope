"use client";

/** A model field that suggests without deciding.
 *
 *  The list and the text box are the same control on purpose. A `<datalist>` gives
 *  the models we know about — priced from the registry, pulled into a local daemon,
 *  or named by an endpoint that was asked — and the input underneath still accepts a
 *  name none of those sources has ever heard of. That case is not exotic: it is a
 *  Claude release that lands before its price does, a model pulled a minute ago, and
 *  every gateway that serves `/chat/completions` and nothing else.
 *
 *  The line beneath is the honest half. It says what is known about the model that is
 *  actually in the box, including when the answer is "nothing" — a blank where a
 *  price would go, rather than a number nobody could check. */

import type { ModelOption, ProviderModels } from "@/app/lib/settings";

/** Dollars per million tokens, or nothing at all. */
function price(o: ModelOption): string | null {
  if (o.input_usd_per_mtok === 0 && o.output_usd_per_mtok === 0) return "free — runs here";
  if (!o.priced) return null;
  return `$${o.input_usd_per_mtok?.toFixed(2)} in · $${o.output_usd_per_mtok?.toFixed(2)} out per Mtok`;
}

function context(o: ModelOption): string | null {
  if (!o.context) return null;
  return o.context >= 1_000_000
    ? `${(o.context / 1_000_000).toFixed(0)}M ctx`
    : `${(o.context / 1_000).toFixed(0)}K ctx`;
}

/** The one-line summary a `<datalist>` row carries beside the id. */
function gist(o: ModelOption, role: string): string {
  const parts = [price(o), context(o), o.note].filter(Boolean) as string[];
  if (o.recommended_for.includes(role)) parts.unshift(`suggested for ${role}`);
  return parts.join(" · ");
}

export default function ModelPicker({
  role, value, catalog, loading, placeholder, disabled = false, onChange,
}: {
  role: "deep" | "fast";
  value: string;
  catalog: ProviderModels | null;
  loading: boolean;
  placeholder: string;
  disabled?: boolean;
  onChange: (v: string) => void;
}) {
  const options = catalog?.options ?? [];
  // Whatever this role should reach for first comes to the top; the rest keep the
  // order the source gave them, which for a daemon is alphabetical and for the
  // registry is expensive-to-cheap.
  const ordered = [
    ...options.filter((o) => o.recommended_for.includes(role)),
    ...options.filter((o) => !o.recommended_for.includes(role)),
  ];
  const listId = `models-${role}`;
  const match = options.find((o) => o.id === value);

  const hint: string = loading
    ? "reading…"
    : match
      ? gist(match, role) || `known to this ${match.source === "installed" ? "machine" : "console"}`
      : value
        ? "not in the list — it will be used exactly as typed, and priced at nothing "
          + "we can vouch for"
        : catalog && !catalog.reachable
          ? catalog.reason
          : ordered.length
            ? `${ordered.length} to pick from${
                ordered[0]?.recommended_for.includes(role)
                  ? `, starting with ${ordered[0].id}` : ""}`
            : catalog?.reason || "type a model name";

  return (
    <>
      <input
        className="inp mono"
        list={listId}
        placeholder={placeholder}
        value={value}
        disabled={disabled}
        spellCheck={false}
        autoComplete="off"
        onChange={(e) => onChange(e.target.value)}
      />
      <datalist id={listId}>
        {ordered.map((o) => (
          <option key={o.id} value={o.id} label={gist(o, role) || undefined} />
        ))}
      </datalist>
      <span className="block mt-1.5 text-[11px] leading-snug text-[var(--haze)]">
        {hint}
      </span>
    </>
  );
}
