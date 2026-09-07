//! Whether there is a newer version, and replacing this copy with it.
//!
//! The shell asks; the page is told. Nothing here is reachable from JavaScript by
//! IPC, and no `updater:` permission is granted to the page — a plugin command
//! called from Rust never reaches the ACL, so checking costs the page nothing. The
//! one thing the page needs to say back is "do it", and it says that by navigating
//! to a path the window's own navigation handler swallows. See `main.rs`.
//!
//! **It installs, but it never asks for a password.** The plugin will: it renames
//! `/Applications/LanceScope.app` aside, unpacks the tarball in its place, and when
//! that rename is refused it escalates with `do shell script … with administrator
//! privileges`. An admin password prompt is a state that has to be named *before* it
//! appears, and a workbench is not the software that gets to teach somebody the
//! habit of typing their password into a surprise. So the directory holding the app
//! is tested for writability first, and a copy that cannot replace itself says so
//! and offers the release page instead of raising the prompt.
//!
//! That fallback is not an error path. Somebody running from a read-only disk image,
//! or from a directory owned by an administrator they are not, is in a perfectly
//! ordinary state — the difference between that and a broken update is exactly what
//! the `manual` state exists to draw.
//!
//! Every release carries a signed, stapled tarball, built by `desktop/sign.sh` after
//! the ticket is stapled rather than by the bundler before it, so what a copy
//! installs here is a build Gatekeeper accepts on arrival. The signature over it is
//! checked against `plugins.updater.pubkey` by the plugin, before any of this runs.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use tauri::{AppHandle, Manager};
use tauri_plugin_updater::{Update, UpdaterExt};

/// How long to leave it before looking again.
///
/// The risk this addresses is somebody demoing a build from six months ago, and a
/// day is well inside that. More often would be a network call on every launch to
/// answer a question whose answer changes a few times a year.
const EVERY: u64 = 60 * 60 * 24;

/// How long the page gets to draw "restarting" before the process is replaced.
///
/// The last frame of an update is the one that says it worked. Without this the
/// window vanishes on the same tick the install finishes, and the only evidence
/// anything happened is that the app came back a different version.
const LAST_WORD: Duration = Duration::from_millis(700);

/// The smallest gap between two progress messages.
///
/// `on_chunk` fires per HTTP chunk, which over a hundred-odd megabytes is thousands
/// of calls. Each one here would be a `window.eval` — a string crossing into the
/// webview and a React render — to move a bar by a third of a pixel.
const TICK: Duration = Duration::from_millis(200);

/// What was found, and whether something is already being done about it.
///
/// The update is kept because `check` and `install` are separated by however long
/// somebody leaves a toast on screen, and re-asking GitHub at the moment of the
/// click would make the button slower than it needs to be. It is not required,
/// though: a copy that has lost it re-checks rather than refusing.
#[derive(Default)]
pub struct Pending {
    found: Mutex<Option<Update>>,
    busy: AtomicBool,
}

fn stamp_path(app: &AppHandle) -> Option<PathBuf> {
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
                let version = update.version.clone();
                if let Some(state) = app.try_state::<Pending>() {
                    *state.found.lock().unwrap() = Some(update);
                }
                say(&app, "available", &version);
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

/// The `.app` this process is running out of.
///
/// `current_exe` is `…/LanceScope.app/Contents/MacOS/lancescope`, and the plugin
/// walks up exactly the same three levels to decide what to replace. Anything else —
/// `cargo run`, a bare binary — has no bundle to swap, and saying which of the two
/// this is matters more than reporting a path.
fn bundle() -> Result<PathBuf, String> {
    let exe = std::env::current_exe().map_err(|e| format!("could not locate this copy: {e}"))?;
    let bundle = exe
        .parent()
        .filter(|p| p.file_name().is_some_and(|n| n == "MacOS"))
        .and_then(Path::parent)
        .filter(|p| p.file_name().is_some_and(|n| n == "Contents"))
        .and_then(Path::parent)
        .filter(|p| p.extension().is_some_and(|e| e == "app"));
    match bundle {
        Some(path) => Ok(path.to_path_buf()),
        None => Err("this is a development build, not an installed application.".into()),
    }
}

/// Whether the app can be replaced without asking anybody for a password.
///
/// The plugin renames the bundle aside and unpacks in its place, so what has to be
/// writable is the *directory holding it* rather than the bundle. Tested by writing
/// rather than by reading permissions: the answer depends on the volume being
/// read-write, on ACLs, and — for a copy Gatekeeper has translocated to a read-only
/// image — on facts no mode bit records.
fn replaceable(app: &Path) -> Result<(), String> {
    let Some(dir) = app.parent() else {
        return Err("this copy is not inside a folder that can be written to.".into());
    };
    let probe = dir.join(".lancescope-update-probe");
    match std::fs::write(&probe, b"") {
        Ok(()) => {
            let _ = std::fs::remove_file(&probe);
            Ok(())
        }
        Err(e) if e.kind() == std::io::ErrorKind::PermissionDenied => Err(format!(
            "{} cannot be written to by this account.",
            dir.display()
        )),
        Err(e) => Err(format!("{} cannot be written to: {e}", dir.display())),
    }
}

/// Replace this copy with the newer one, and come back as it.
///
/// Called from the window's navigation handler, which is to say: from a button
/// somebody pressed in the toast. Everything that could refuse — no bundle, a
/// directory this account cannot write, an update that has gone away — refuses
/// *before* a hundred megabytes are fetched.
pub fn install(app: AppHandle) {
    let Some(state) = app.try_state::<Pending>() else {
        return;
    };
    // A navigation is cheap to repeat and a double-click is one. Two installs
    // unpacking over one bundle is the worst outcome on the menu.
    if state.busy.swap(true, Ordering::SeqCst) {
        return;
    }

    let ready = bundle().and_then(|path| replaceable(&path));
    if let Err(why) = ready {
        say(&app, "manual", &why);
        state.busy.store(false, Ordering::SeqCst);
        return;
    }

    tauri::async_runtime::spawn(async move {
        let held = app
            .try_state::<Pending>()
            .and_then(|s| s.found.lock().unwrap().take());

        // Re-asking is the cost of a toast somebody left open overnight. It is one
        // request, and it beats a button that reports failure because the process
        // forgot what it had already been told.
        let found = match held {
            Some(update) => Ok(Some(update)),
            None => match app.updater() {
                Ok(updater) => updater.check().await,
                Err(e) => Err(e),
            },
        };

        let update = match found {
            Ok(Some(update)) => update,
            Ok(None) => {
                say(&app, "current", env!("CARGO_PKG_VERSION"));
                free(&app);
                return;
            }
            Err(e) => {
                say(&app, "failed", &e.to_string());
                free(&app);
                return;
            }
        };

        let version = update.version.clone();
        say(&app, "downloading", "0");

        let mut had: u64 = 0;
        let mut last = Instant::now();
        // Nothing is said until a chunk carries a length to say it against. A
        // percentage invented from a missing content-length is a bar that lies
        // rather than one that waits.
        let mut shown: u8 = 0;

        let outcome = update
            .download_and_install(
                |chunk, total| {
                    had += chunk as u64;
                    let Some(total) = total.filter(|t| *t > 0) else {
                        return;
                    };
                    let pct = ((had.min(total) * 100) / total) as u8;
                    if pct == shown || last.elapsed() < TICK {
                        return;
                    }
                    shown = pct;
                    last = Instant::now();
                    say(&app, "downloading", &pct.to_string());
                },
                || say(&app, "installing", env!("CARGO_PKG_VERSION")),
            )
            .await;

        if let Err(e) = outcome {
            println!("[shell] update install failed: {e}");
            say(&app, "failed", &e.to_string());
            free(&app);
            return;
        }

        say(&app, "installed", &version);
        std::thread::sleep(LAST_WORD);

        // Explicitly, and belt-and-braces: the exit hook in `main` kills it too, but
        // a server left holding its port outlives the window that owned it, and the
        // next thing this process does is start a second one.
        if let Some(server) = app.try_state::<crate::Server>() {
            if let Some(mut child) = server.0.lock().unwrap().take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }

        // From this thread rather than the main one, and that is not incidental.
        // `restart` on the main thread skips `ExitRequested` and `Exit` and spawns
        // the replacement immediately — while this process is still alive and still
        // holding the single-instance socket, which the new copy would connect to,
        // conclude it is the second launch of an app that is already running, and
        // exit. The window would simply never come back. Called from anywhere else
        // it asks the runtime to exit and restarts on the way out, which is when the
        // plugin releases that socket.
        app.restart();
    });
}

/// Let go, so the button works again after a refusal.
fn free(app: &AppHandle) {
    if let Some(state) = app.try_state::<Pending>() {
        state.busy.store(false, Ordering::SeqCst);
    }
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
