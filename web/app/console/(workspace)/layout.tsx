"use client";

/** The workspace shell, wrapped around every route that wants it.
 *
 *  A route group, so the file tree changes and not one URL: `/console` and
 *  `/console/new` are still exactly where they were. `/console/settings` and
 *  `/console/bundle` are siblings *outside* this group, deliberately — see
 *  `ConsoleShell` for why neither wants this chrome.
 *
 *  Booting happens here rather than in a page because a layout survives navigation
 *  and a page does not. The table list, the settings and what the language layer can
 *  do are fetched once and stay fetched; moving to the ingest wizard and back used
 *  to re-fetch all three and show an empty console until they landed.
 */

import { useEffect } from "react";

import ConsoleShell from "@/app/components/console/shell/ConsoleShell";
import { listTables } from "@/app/lib/catalog";
import { getCapabilities, getSettings } from "@/app/lib/settings";
import { recordCost, set } from "@/app/lib/workspace";

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    listTables()
      .then((d) => {
        set({ list: d, listError: null });
        // The first entry in the ledger, and the one that makes the point: listing
        // every table reads manifests and never data.
        recordCost("list tables", d.read_bytes, d.read_iops);
      })
      .catch((e) => set({ listError: e instanceof Error ? e.message : "unreachable" }));

    getSettings().then((s) => set({ settings: s })).catch(() => set({ settings: null }));
    getCapabilities().then((c) => set({ ai: c })).catch(() => set({ ai: null }));

    // The console links to the demo only where there is a demo to link to. The
    // corpus is gigabytes and is not shipped, so in most builds there is not one.
    fetch("/api/health", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => set({ demoReady: Boolean(d?.ok) }))
      .catch(() => set({ demoReady: false }));
  }, []);

  return <ConsoleShell>{children}</ConsoleShell>;
}
