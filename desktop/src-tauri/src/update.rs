//! Whether there is a newer version, said rather than installed.
//!
//! The shell asks; the page is told. Nothing here is reachable from JavaScript, and
//! no `updater:` permission is granted to it — a plugin command called from Rust
//! never reaches the ACL, so checking costs the page nothing.
//!
//! **It does not install.** The plugin can: it unpacks the tarball over
//! `/Applications/LanceScope.app` and, when that is refused, escalates with
//! `do shell script … with administrator privileges`. An admin password prompt is a
//! state that has to be named *before* it appears, not discovered — and there is
//! nowhere yet to name it. So this reports, and the button opens the page where the
//! release is, which is a thing somebody chose to click.
//!
//! That is also why the artifact side landed first. Every release from now on
//! carries a signed, stapled tarball, so the day installing is wired up there is
//! something for the copies already out there to update *to*. A release published
//! without one is a version nobody can ever leave.

use std::time::{SystemTime, UNIX_EPOCH};

use tauri::{AppHandle, Manager};
use tauri_plugin_updater::UpdaterExt;

/// How long to leave it before looking again.
///
/// The risk this addresses is somebody demoing a build from six months ago, and a
/// day is well inside that. More often would be a network call on every launch to
/// answer a question whose answer changes a few times a year.
const EVERY: u64 = 60 * 60 * 24;

fn stamp_path(app: &AppHandle) -> Option<std::path::PathBuf> {
    app.path()
        .app_config_dir()
        .ok()
        .map(|d| d.join("update-check"))
}

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Whether enough time has passed. Any failure to read the stamp means "yes" —
/// a check that silently stopped happening is worse than one that happens twice.
fn due(app: &AppHandle) -> bool {
    let Some(path) = stamp_path(app) else {
        return true;
    };
    let Ok(text) = std::fs::read_to_string(path) else {
        return true;
    };
    let Ok(last) = text.trim().parse::<u64>() else {
        return true;
    };
    now().saturating_sub(last) >= EVERY
}

fn touch(app: &AppHandle) {
    let Some(path) = stamp_path(app) else { return };
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let _ = std::fs::write(path, now().to_string());
}

/// Ask, and tell the page what came back.
///
/// `manual` skips the throttle and reports "you are up to date", because a menu item
/// that does nothing visible is indistinguishable from one that is broken. The
/// launch check stays quiet unless there is something to say.
pub fn check(app: AppHandle, manual: bool) {
    if !manual && !due(&app) {
        return;
    }
    tauri::async_runtime::spawn(async move {
        touch(&app);
        let outcome = match app.updater() {
            Ok(updater) => updater.check().await,
            Err(e) => Err(e),
        };
        match outcome {
            Ok(Some(update)) => {
                say(&app, "available", &update.version);
            }
            Ok(None) => {
                if manual {
                    say(&app, "current", env!("CARGO_PKG_VERSION"));
                }
            }
            Err(e) => {
                // Named rather than swallowed, but only when somebody asked. A
                // launch check that cannot reach GitHub is not worth interrupting
                // anybody about; a menu item that did nothing is.
                println!("[shell] update check failed: {e}");
                if manual {
                    say(&app, "failed", &e.to_string());
                }
            }
        }
    });
}

/// Hand the answer to the page as an event.
///
/// `eval` rather than IPC, so this needs no capability. The page decides how to show
/// it; the shell does not draw anything, because a native alert over a console that
/// has its own way of naming states would be a second vocabulary.
fn say(app: &AppHandle, state: &str, detail: &str) {
    let Some(window) = app.get_webview_window("main") else {
        return;
    };
    let js = format!(
        "window.dispatchEvent(new CustomEvent('lancescope:update',{{detail:{{state:{},version:{}}}}}))",
        json_string(state),
        json_string(detail),
    );
    let _ = window.eval(js);
}

fn json_string(s: &str) -> String {
    serde_json::Value::String(s.to_string()).to_string()
}
