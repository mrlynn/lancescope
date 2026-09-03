/** Naming a database.
 *
 *  The header used to print the whole root path, which is the one string on the
 *  page guaranteed to be long, unstable and identical between two different
 *  databases right up to its last segment. What identifies a database to the
 *  person reading it is its name; the path is provenance, and belongs in a
 *  tooltip and on the settings page, not in the chrome of every screen.
 */

/** The last meaningful segment of a local path or a `s3://` / `db://` URI.
 *  `/Users/x/code/lancedb/data/lance` → `lance`; `s3://acme-vectors` → `acme-vectors`. */
export function dbName(uri: string | null | undefined): string | null {
  if (!uri) return null;
  const trimmed = uri.replace(/\/+$/, "");
  if (!trimmed) return "/";
  // Strip a scheme, then take the last segment. A bucket with no prefix leaves
  // the bucket itself, which is the right name for it.
  const withoutScheme = trimmed.replace(/^[a-z0-9+.-]+:\/\//i, "");
  const parts = withoutScheme.split("/").filter(Boolean);

  // A Hub root is named by its repository, not by its last path segment. Every
  // dataset LanceDB publishes stores its tables under `data/`, so the general rule
  // named all of them "data" — the switcher said it, the home page said it, and
  // the public demo's banner said "pinned to data", which tells a stranger nothing
  // at all. `hf://datasets/<org>/<repo>/<path>` is the shape; the repo is the name
  // a person would use for it. Same split server/hf.py parses.
  if (/^hf:\/\/datasets\//i.test(trimmed) && parts.length >= 3) {
    // parts: ["datasets", org, repo, ...path]
    return parts[2];
  }

  const last = parts.pop();
  return last ?? withoutScheme ?? null;
}

/** Everything before the name, for the second line of a switcher row. Empty when
 *  the name is the whole thing. */
export function dbParent(uri: string | null | undefined): string {
  if (!uri) return "";
  const trimmed = uri.replace(/\/+$/, "");
  const name = dbName(trimmed);
  if (!name) return trimmed;
  const cut = trimmed.lastIndexOf(name);
  return cut <= 0 ? "" : trimmed.slice(0, cut);
}

/** Where a root came from, in one short phrase — the same four rungs the settings
 *  page explains at length. Used as the chip's tooltip. */
export const ROOT_SOURCE: Record<string, string> = {
  env: "from the LANCE_ROOT environment variable",
  connection: "the active saved connection",
  default: "the demo corpus, as a first-run fallback",
  none: "nothing configured",
};
