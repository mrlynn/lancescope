"use client";

/** The seam for a native directory picker.
 *
 *  There is none today, in either surface. The browser has no API that returns a
 *  filesystem path, and the desktop shell opens an external `http://127.0.0.1:<port>`
 *  URL with no Tauri IPC bridge injected — `desktop/src-tauri/src/main.rs` has zero
 *  `#[tauri::command]` functions. Adding one means a dialog plugin, a first command,
 *  a capability granting IPC to a remote origin, an init script, and a fresh look at
 *  signing and entitlements: a security decision, for one input field that already
 *  works by pasting a path.
 *
 *  So this returns `null` and `pickerAvailable()` is false, the Browse button does
 *  not render, and the day that changes there is exactly one call site to change.
 */

type TauriWindow = Window & { __TAURI__?: unknown };

export function pickerAvailable(): boolean {
  return typeof window !== "undefined" && (window as TauriWindow).__TAURI__ !== undefined;
}

export async function pickDirectory(): Promise<string | null> {
  return null;
}
