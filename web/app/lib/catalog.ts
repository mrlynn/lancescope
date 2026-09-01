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

export type TableList = {
  root: string;
  tables: TableRef[];
  read_bytes: number;
  read_iops: number;
  note: string;
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

export const listTables = () => get<TableList>("/tables");
export const getTable = (n: string) => get<TableDetail>(`/tables/${n}`);
export const getVersions = (n: string) => get<Versions>(`/tables/${n}/versions`);
export const getIndices = (n: string) => get<Indices>(`/tables/${n}/indices`);
export const getFragments = (n: string) => get<Fragments>(`/tables/${n}/fragments`);

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
