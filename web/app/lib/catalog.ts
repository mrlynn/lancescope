// Client for the read-only console API. Shapes mirror server/routes/catalog.py.

export type TableRef = {
  name: string;
  uri: string;
  rows: number;
  version: number;
  latest_version: number;
  storage_version: string;
  fragments: number;
  small_files: number;
  deleted_rows: number;
  indices: number;
  columns: number;
  blob_columns: string[];
  manifest_bytes: number;
  modified: string | null;
};

/** What a connection can honestly do, decided from what it is rather than by
 *  trying. Three states, because "unsupported" and "never tried" are different
 *  claims and reporting the second as the first is a guess. */
export type Capability = {
  state: "available" | "unsupported" | "unverified";
  reason: string;
  available: boolean;
};

export type RootCapabilities = {
  remote: boolean;
  discover: Capability;
  inspect: Capability;
  disk_split: Capability;
  io_meter: Capability;
};

export type TableList = {
  root: string;
  tables: TableRef[];
  capabilities?: RootCapabilities;
  read_bytes: number;
  read_iops: number;
  note: string;
  /** Why the listing is short, when the reason is not that the database is small.
   *  Null on a listing that succeeded — including one that succeeded and found
   *  nothing, which is a fact about the database rather than a failure to read it.
   *  A remote root can fail to list for reasons that have nothing to do with what
   *  is in it, and "no tables here" is the wrong sentence for all of them. */
  listing_error?: string | null;
};

export type Field = {
  name: string;
  type: string;
  nullable: boolean;
  blob: boolean;
  metadata: Record<string, string>;
};

export type TableDetail = {
  name: string;
  uri: string;
  rows: number;
  version: number;
  latest_version: number;
  storage_version: string;
  modified: string | null;
  fields: Field[];
  blob_columns: string[];
  stats: {
    num_fragments: number;
    num_small_files: number;
    num_deleted_rows: number;
    num_indices: number;
  };
  manifest_bytes: number;
  on_disk: { blob_bytes: number; meta_bytes: number; ratio: number; files: number };
  read_bytes: number;
  read_iops: number;
};

export type VersionEntry = {
  version: number;
  timestamp: string | null;
  operation: string | null;
  rows: number;
  fragments: number;
  data_files: number;
  deleted_rows: number;
  manifest_bytes: number;
  diff: null | {
    rows: number;
    fragments: number;
    data_files: number;
    deleted_rows: number;
    manifest_bytes: number;
  };
};

export type Versions = {
  name: string;
  current_version: number;
  latest_version: number;
  versions: VersionEntry[];
  tags: Record<string, unknown>;
  branches: Record<string, unknown>;
  read_bytes: number;
  read_iops: number;
};

export type IndexEntry = {
  name: string;
  type: string;
  uuid: string | null;
  columns: string[];
  version: number | null;
  fragment_ids: number[];
  indexed_rows: number | null;
  unindexed_rows: number | null;
  num_indices: number | null;
  updated_at_ms: number | null;
  coverage: number | null;
  params: Record<string, unknown> | null;
};

export type Indices = {
  name: string;
  rows: number;
  indices: IndexEntry[];
  unindexed_columns: {
    name: string;
    type: string;
    blob: boolean;
    vector_dim: number | null;
    indexable: boolean;
  }[];
  unindexed_vector_columns: string[];
  read_bytes: number;
  read_iops: number;
};

export type Fragments = {
  name: string;
  rows: number;
  fragments: {
    id: number;
    rows: number;
    physical_rows: number;
    deleted_rows: number;
    data_files: {
      path: string;
      size_bytes: number;
      blob_bytes: number;
      blob_files: number;
      columns: number[];
      file_version: string;
    }[];
    data_bytes: number;
    blob_bytes: number;
    blob_files: number;
    total_bytes: number;
  }[];
  stats: { num_fragments: number; num_small_files: number; num_deleted_rows: number };
  has_blob_columns: boolean;
  small_files_note: string | null;
  read_bytes: number;
  read_iops: number;
};

// A cell is a scalar, or one of the summaries the server substitutes for a column
// it declined to materialise.
export type Cell =
  | string
  | number
  | boolean
  | null
  | { blob: true; size_bytes: number | null; position: number | null; materialised: false }
  | { bytes: number; materialised: true }
  | { vector_dim: number; head: number[] };

export type Rows = {
  name: string;
  offset: number;
  limit: number;
  returned: number;
  total_rows: number;
  filter: string | null;
  columns: string[];
  omitted_columns: {
    name: string;
    type: string;
    vector_dim: number | null;
    reason: string;
  }[];
  rows: Record<string, Cell>[];
  read_bytes: number;
  read_iops: number;
};

/** Whose question a finding answers. A panel says where the evidence lives; a facet
 *  says who is paying for it, and the two are different axes — an unindexed vector
 *  column is evidence on Indices and a per-query cost to anyone running an eval. */
export type Facet = "training";

/** A finding is derived, never generated: `evidence` holds the literal numbers the
 *  claim was computed from, and `panel` names where those numbers are on screen. */
export type Finding = {
  id: string;
  severity: "warn" | "note";
  panel: "schema" | "versions" | "indices" | "fragments" | "rows";
  title: string;
  claim: string;
  evidence: Record<string, unknown>;
  caveat: string;
  suggested_action: string;
  columns: string[];
  facets: Facet[];
};

/** A check that could not run. Distinct from "nothing to report": a partial
 *  analysis has to look different from a clean one, or a broken rule reads as good
 *  news. */
export type RuleFailure = {
  rule: string;
  error: string;
  message: string;
};

export type Findings = {
  name: string;
  uri: string;
  /** Which facet this response was narrowed to, or null for everything. */
  facet: Facet | null;
  findings: Finding[];
  summary: {
    total: number;
    warn: number;
    note: number;
    by_panel: Record<string, number>;
  };
  partial_analysis: boolean;
  failed_rules: RuleFailure[];
  read_bytes: number;
  read_iops: number;
};

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`/api/catalog${path}`, { cache: "no-store" });
  if (!res.ok) {
    // FastAPI puts the reason in `detail`; surfacing it is the difference between
    // "400" and "no field named trak".
    let detail = `${res.status}`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, String(detail));
  }
  return res.json();
}

/** Which Lance is underneath, and what that build of it can do.
 *
 *  Answered without opening a dataset, so it renders on a console with nothing
 *  configured — which is exactly when a reader needs to know whether a panel is
 *  empty because the database is or because the reader cannot see into it. */
export type RuntimeFeature = {
  name: string;
  supported: boolean;
  probe: string;
  lost: string | null;
};

export type RuntimeReport = {
  versions: { lance: string; pyarrow: string; python: string };
  features: RuntimeFeature[];
  summary: string | null;
  /** Whether this process is the public demo rather than someone's own console.
   *  Carried here because every screen already has a reason to ask what the
   *  runtime is, and a second request to learn one boolean would be silly. */
  kiosk: boolean;
};

export const getRuntime = () => get<RuntimeReport>("/runtime");

export const listTables = () => get<TableList>("/tables");
export const getTable = (n: string) => get<TableDetail>(`/tables/${n}`);
export const getVersions = (n: string) => get<Versions>(`/tables/${n}/versions`);
export const getIndices = (n: string) => get<Indices>(`/tables/${n}/indices`);
export const getFragments = (n: string) => get<Fragments>(`/tables/${n}/fragments`);
/** Every finding, once per table. The facets ride along on each one, so a view that
 *  wants a subset filters what is already here — `?facet=` exists on the route for
 *  callers with no list in hand, which is agents over MCP rather than this page. */
export const getFindings = (n: string) => get<Findings>(`/tables/${n}/findings`);

export function getRows(
  n: string,
  opts: { offset?: number; limit?: number; filter?: string | null; expand?: string[] } = {},
): Promise<Rows> {
  const q = new URLSearchParams();
  q.set("offset", String(opts.offset ?? 0));
  q.set("limit", String(opts.limit ?? 25));
  if (opts.filter) q.set("filter", opts.filter);
  if (opts.expand?.length) q.set("expand", opts.expand.join(","));
  return get<Rows>(`/tables/${n}/rows?${q}`);
}

/* ------------------------------------------------------------------- query */

/** What a table can be asked. A mode that cannot run carries a reason, because a
 *  disabled control that explains itself beats a search that silently finds
 *  nothing. */
export type QueryCapability = {
  mode: "scan" | "fts" | "vector" | "hybrid";
  available: boolean;
  reason: string;
  columns: string[];
};

export type QueryCapabilities = {
  name: string;
  capabilities: QueryCapability[];
  read_bytes: number;
  read_iops: number;
};

/** The access path Lance chose, lifted out of the plan. The raw plan travels with
 *  it: Lance owns that format, so a partial reading beside the real thing degrades
 *  into "we recognised less of it" rather than into being wrong. */
export type PlanReading = {
  text: string;
  paths: { operator: string; name: string; meaning: string }[];
  pushed_down_filter: string | null;
  fragments: number | null;
};

export type QuerySpec = {
  mode: "scan" | "fts" | "vector" | "hybrid";
  filter?: string | null;
  columns?: string[] | null;
  limit?: number;
  offset?: number;
  text?: string | null;
  vector_column?: string | null;
  vector?: number[] | null;
  like_row?: number | null;
  k?: number;
  metric?: string;
  prefilter?: boolean;
};

export type QueryResult = {
  name: string;
  uri: string;
  mode: string;
  rows: Record<string, Cell>[];
  columns: string[];
  omitted_columns: { name: string; type: string; vector_dim: number | null; reason: string }[];
  plan: PlanReading;
  ms: number;
  read_bytes: number;
  read_iops: number;
  returned: number;
  total_rows: number | null;
  truncated: boolean;
  reproduction: string;
  /** The version this result describes, and the newest on disk when it was read.
   *  They differ when the table has been written to since, which makes everything
   *  on screen true of a version nobody is using any more. */
  version: number;
  latest_version: number;
  stale: boolean;
  /** Only a hybrid search has legs. Reported separately because its cost is the sum
   *  of two paths, and one of them may be a brute-force scan that dominates. */
  legs: {
    mode: string;
    plan: PlanReading;
    ms: number;
    read_bytes: number;
    read_iops: number;
    returned: number;
  }[];
};

export const getQueryCapabilities = (n: string) =>
  get<QueryCapabilities>(`/tables/${n}/query/capabilities`);

/** One column, as something to finish typing rather than as something to display.
 *  Shapes mirror `Column` in server/query.py. */
export type CompletionColumn = {
  name: string;
  type: string;
  /** string | number | boolean | temporal | vector | blob | other. Decides which
   *  operators are offered and whether a value needs quoting. */
  kind: string;
  filterable: boolean;
  operators: string[];
  /** Already rendered as SQL literals, quotes and all, ready to insert. Empty for
   *  a column that is not a facet — which is not the same as one with no values. */
  values: string[];
  /** Whether `values` is the whole column or what a sample found. The dropdown says
   *  which, because "these are the values" is a bigger promise than we can keep on
   *  a table too large to read. */
  values_complete: boolean;
  values_scanned: number;
};

export type QueryCompletions = {
  name: string;
  columns: CompletionColumn[];
  rows: number;
  values_included: boolean;
  read_bytes: number;
  read_iops: number;
};

export type FilterValidation = {
  valid: boolean;
  error: string | null;
  filter: string;
  matched_rows: number | null;
  total_rows: number | null;
  read_bytes: number;
  read_iops: number;
};

/** Read once when the workspace opens, so finishing a predicate is local rather
 *  than a request per keystroke. */
export const getQueryCompletions = (n: string, values = true) =>
  get<QueryCompletions>(`/tables/${n}/query/completions?values=${values}`);

/** Where the bytes of one heavy cell live.
 *
 *  A URL rather than a fetch, because the caller decides what to do with it — read
 *  it to get at `X-Read-Bytes`, or hand it straight to a `<video>` that will make
 *  its own range requests as somebody scrubs.
 *
 *  Nothing here reads heavy columns on its own. Asking for this URL is somebody
 *  deciding to spend the bytes, and the response says how many it took.
 */
export function heavyCellUrl(table: string, column: string, row: RowAddress): string {
  const q = new URLSearchParams({ column });
  if ("rowid" in row) {
    q.set("rowid", String(row.rowid));
  } else {
    q.set("key_column", row.keyColumn);
    q.set("key", String(row.key));
  }
  return `/api/catalog/tables/${table}/blob?${q}`;
}

/** How to name one row. A row id is exact and every row browse and query result
 *  now carries one; a key lookup is for a value someone is holding, and for a URL
 *  that has to survive being written down. */
export type RowAddress =
  | { rowid: number }
  | { keyColumn: string; key: string | number };

async function post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`/api/catalog${path}`, {
    method: "POST",
    cache: "no-store",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, String(detail));
  }
  return res.json();
}

/** Does this predicate parse, and how many rows does it match. Asked while someone
 *  is still typing, so an invalid filter is an ordinary answer rather than a throw. */
export const validateFilter = (n: string, filter: string, signal?: AbortSignal) =>
  post<FilterValidation>(`/tables/${n}/query/validate`, { filter }, signal);

export const runQuery = (n: string, spec: QuerySpec, signal?: AbortSignal) =>
  post<QueryResult>(`/tables/${n}/query`, spec, signal);

export const explainQuery = (n: string, spec: QuerySpec) =>
  post<{ plan: PlanReading; read_bytes: number }>(`/tables/${n}/query/explain`, spec);

/* ----------------------------------------------------------------- compare */

export type CompareSide = {
  version: number;
  timestamp: string | null;
  operation: string | null;
  rows: number;
  fields: Record<string, string>;
  indices: Record<string, { type: string; columns: string[]; fragments: number }>;
  fragments: number;
  small_files: number;
  deleted_rows: number;
  blob_bytes: number;
  meta_bytes: number;
};

export type CompareDiff = {
  schema: { added: string[]; removed: string[]; retyped: Record<string, { from: string; to: string }> };
  indices: {
    added: string[];
    removed: string[];
    changed: Record<string, { from: unknown; to: unknown }>;
  };
  rows: number;
  fragments: number;
  small_files: number;
  deleted_rows: number;
  blob_bytes: number;
  meta_bytes: number;
  unchanged: boolean;
  on_disk_note: string;
};

export type Comparison = {
  name: string;
  a: CompareSide;
  b: CompareSide;
  diff: CompareDiff;
  read_bytes: number;
  read_iops: number;
};

/** Either side may have refused the query, and that is a result. A full-text search
 *  against the version before its index existed cannot run at all — the most useful
 *  before/after there is, and one thrown away by treating a refusal as an error. */
export type QueryComparison = {
  name: string;
  versions: { a: number; b: number };
  a: QueryResult | null;
  b: QueryResult | null;
  a_error: string | null;
  b_error: string | null;
  ran_both: boolean;
  verdict?: string;
  bytes_delta?: number;
  ms_delta?: number;
  paths_changed?: boolean;
  bytes_ratio?: number | null;
};

export const compareVersions = (n: string, a: number, b: number) =>
  get<Comparison>(`/tables/${n}/compare?a=${a}&b=${b}`);

export const compareQuery = (n: string, a: number, b: number, spec: QuerySpec) =>
  post<QueryComparison>(`/tables/${n}/compare/query`, { ...spec, a, b });
