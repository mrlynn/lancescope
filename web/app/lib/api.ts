export type Hit = {
  moment_id: string;
  talk_id: string;
  title: string;
  speaker: string;
  track: string;
  year: number;
  ts_s: number;
  segment_idx: number;
  segment_offset_s: number;
  transcript: string;
  thumb: string | null;
  video_url: string;
  score?: number;
};

export type MeterState = {
  index_bytes: number;
  index_iops: number;
  video_bytes: number;
  video_iops: number;
  corpus_video_bytes: number;
  corpus_moments: number;
  corpus_talks: number;
  median_talk_bytes: number;
  median_segment_bytes: number;
  rev: number;
};

export type SearchResponse = {
  hits: Hit[];
  ms: number;
  query_index_bytes: number;
  query_video_bytes: number;
  meter: MeterState;
};

export async function search(body: {
  q: string;
  mode: string;
  limit?: number;
  year?: number | null;
  speaker?: string | null;
  track?: string | null;
}): Promise<SearchResponse> {
  const res = await fetch("/api/search", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`search failed: ${res.status}`);
  return res.json();
}

export async function resetMeter(): Promise<MeterState> {
  const res = await fetch("/api/meter/reset", { method: "POST" });
  return res.json();
}

export function fmtBytes(n: number): { value: string; unit: string } {
  if (n < 1000) return { value: String(n), unit: "B" };
  if (n < 1_000_000) return { value: (n / 1000).toFixed(1), unit: "KB" };
  if (n < 1_000_000_000) return { value: (n / 1e6).toFixed(1), unit: "MB" };
  return { value: (n / 1e9).toFixed(2), unit: "GB" };
}

export function fmtClock(s: number): string {
  const m = Math.floor(s / 60);
  const r = Math.floor(s % 60);
  return `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}
