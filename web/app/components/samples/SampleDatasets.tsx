"use client";

/** Public datasets to open when you have nothing of your own to look at.
 *
 *  The shape is Compass's — sample data offered rather than installed — but the
 *  bargain here is better and the copy should say so. Compass downloads a corpus
 *  into your cluster; this saves a URI. pylance reads `hf://` lazily, so opening a
 *  million-row video table costs 24 KB and searching it never touches a frame.
 *  "Nothing is downloaded" is the single most important sentence on the screen,
 *  because the reason people decline sample data is that it looks expensive.
 *
 *  Six, curated, each with a measured line and a reason to open *this* one. The org
 *  publishes forty-eight, and forty-eight unannotated names is a directory rather
 *  than an offer.
 */

import { useEffect, useState } from "react";

import Icon from "@/app/components/Icon";
import { getSamples, openSample, type Sample } from "@/app/lib/settings";

export default function SampleDatasets({
  onOpened, compact = false,
}: {
  onOpened: (uri: string) => void;
  compact?: boolean;
}) {
  const [list, setList] = useState<Sample[] | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Saving a connection and switching to it are two things, and `LANCE_ROOT` makes
  // the second one refuse. Reloading anyway would leave someone looking at the same
  // empty database wondering what the button did.
  const [refused, setRefused] = useState<string | null>(null);

  useEffect(() => {
    getSamples()
      .then((d) => { setList(d.samples); setNote(d.note); })
      .catch((e) => setError(String(e)));
  }, []);

  const open = async (s: Sample) => {
    setBusy(s.uri);
    setError(null);
    setRefused(null);
    try {
      const outcome = await openSample(s.uri);
      if (!outcome.adopted) {
        setRefused(outcome.note);
        return;
      }
      onOpened(s.uri);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  if (error) {
    return (
      <p className="text-[12px] leading-relaxed" style={{ color: "var(--video)" }}>
        {error}
      </p>
    );
  }
  if (!list) return <div className="eyebrow">loading</div>;

  const shown = compact ? list.slice(0, 3) : list;

  return (
    <div>
      <p className="text-[13px] text-[var(--haze)] leading-relaxed mb-5 max-w-2xl">
        {note}
      </p>

      {refused && (
        <p className="text-[12px] leading-relaxed mb-5 px-3.5 py-3 rounded-sm max-w-2xl"
           style={{ background: "rgb(var(--index-rgb) / 0.07)",
                    border: "1px solid rgb(var(--index-rgb) / 0.28)",
                    color: "var(--body)" }}>
          {refused}
        </p>
      )}

      <div className="grid gap-3 md:grid-cols-2">
        {shown.map((s) => (
          <div key={s.uri} className="panel px-4 py-3.5 flex flex-col gap-2 text-left">
            <div className="flex items-baseline gap-2.5">
              <span className="text-[14px] text-[var(--bright)]">{s.title}</span>
              {s.added && (
                <span className="eyebrow" style={{ color: "var(--index)" }}>added</span>
              )}
            </div>
            <p className="text-[12px] text-[var(--body)] leading-relaxed">{s.what}</p>
            <p className="text-[12px] text-[var(--haze)] leading-relaxed">{s.shows}</p>
            <p className="mono text-[10px] text-[var(--haze)] mt-auto pt-1">{s.scale}</p>
            <button
              className={s.added ? "btn mt-1 self-start" : "btn btn-accent mt-1 self-start"}
              disabled={busy !== null}
              onClick={() => open(s)}
            >
              <Icon name={busy === s.uri ? "clock" : "database"} size={13} />
              {busy === s.uri ? "Opening…" : s.added ? "Switch to it" : "Open it"}
            </button>
          </div>
        ))}
      </div>

      {compact && list.length > shown.length && (
        <p className="text-[12px] text-[var(--haze)] mt-4">
          {list.length - shown.length} more on the{" "}
          <a href="/console/settings" className="underline" style={{ color: "var(--video)" }}>
            settings page
          </a>.
        </p>
      )}
    </div>
  );
}
