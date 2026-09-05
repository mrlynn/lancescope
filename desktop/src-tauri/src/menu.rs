//! The menu bar, and the two things in it that are not decoration.
//!
//! **Edit is not optional.** Without a standard Edit menu, ⌘C and ⌘V in a
//! WKWebView are unreliable, and this is a console full of paths, filters and
//! reproduction snippets that exist to be copied.
//!
//! **Open Recent reads the server's file, and writes through the server's API.**
//! `server/settings.py` owns which databases exist and which one is active: the id,
//! the label, `last_used`, and a resolution ladder where `LANCE_ROOT` beats
//! everything. None of that is copied here. The menu reads that file to draw itself
//! and then asks the server to switch, because activation is not a write to a field
//! — it repoints the live catalog and stamps `last_used` — and a second
//! implementation of it would be a second opinion about which database is open.
//! `server/headless.py` makes the same argument about the CLI, in the same words.
//!
//! Nothing here needs a capability. Every menu action is Rust calling either the
//! server over loopback or `eval` into a page, and `eval` is not IPC.

use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::PathBuf;
use std::time::Duration;

use tauri::menu::{Menu, MenuItemBuilder, PredefinedMenuItem, SubmenuBuilder};
use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::DialogExt;

/// A saved connection, as much of one as a menu needs.
struct Recent {
    id: String,
    label: String,
    uri: String,
}

/// Where the server keeps its settings. The same ladder as
/// `server/settings.py::settings_path`, and it has to stay the same ladder: a menu
/// reading a different file from the one the console writes would offer databases
/// that are not there.
fn settings_path() -> Option<PathBuf> {
    if let Ok(explicit) = std::env::var("LANCESCOPE_CONFIG") {
        return Some(PathBuf::from(explicit));
    }
    let base = match std::env::var("XDG_CONFIG_HOME") {
        Ok(dir) if !dir.is_empty() => PathBuf::from(dir),
        _ => PathBuf::from(std::env::var("HOME").ok()?).join(".config"),
    };
    Some(base.join("lancescope").join("settings.json"))
}

/// The saved connections, most recently used first.
///
/// Every failure here is an empty list rather than an error: a menu is not the
/// place to learn that a settings file is malformed, and the console says so
/// properly on a screen with room to explain.
fn recents() -> Vec<Recent> {
    let Some(path) = settings_path() else {
        return Vec::new();
    };
    let Ok(text) = std::fs::read_to_string(path) else {
        return Vec::new();
    };
    parse_recents(&text)
}

/// The parsing half, separated from the reading half so it can be tested against a
/// settings file written out by hand.
fn parse_recents(text: &str) -> Vec<Recent> {
    let Ok(value) = serde_json::from_str::<serde_json::Value>(text) else {
        return Vec::new();
    };
    let Some(list) = value.get("connections").and_then(|c| c.as_array()) else {
        return Vec::new();
    };

    let mut out: Vec<(String, Recent)> = list
        .iter()
        .filter_map(|c| {
            Some((
                c.get("last_used")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                Recent {
                    id: c.get("id")?.as_str()?.to_string(),
                    label: c.get("label")?.as_str()?.to_string(),
                    uri: c.get("uri")?.as_str()?.to_string(),
                },
            ))
        })
        .collect();
    // Timestamps are ISO 8601 in UTC, which sorts correctly as text. Reversed, so
    // the one you used last is the one nearest the cursor.
    out.sort_by(|a, b| b.0.cmp(&a.0));
    out.into_iter().map(|(_, r)| r).collect()
}

/// Whether `LANCE_ROOT` is pinning the root.
///
/// When it is, the saved connections are inert — `resolve_root` never reaches them
/// — and the settings page greys the list out rather than letting somebody edit
/// something with no effect. A menu that silently did nothing would be the same bug
/// in a different widget.
fn env_locked() -> bool {
    std::env::var("LANCE_ROOT").is_ok_and(|v| !v.is_empty())
}

/// One request to our own server, over loopback.
///
/// Hand-written rather than reached for a client, because this is the whole of what
/// it has to do: one POST, to one host that is this process's own child, on a port
/// we chose, with no redirects, no TLS and no proxy in between. An HTTP client is a
/// lot of dependency for that.
fn post(port: u16, path: &str, body: &str) -> Result<(), String> {
    let mut stream = TcpStream::connect(("127.0.0.1", port))
        .map_err(|e| format!("could not reach the server: {e}"))?;
    let _ = stream.set_read_timeout(Some(Duration::from_secs(15)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(15)));

    let request = format!(
        "POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\
         Content-Type: application/json\r\nContent-Length: {}\r\n\
         Connection: close\r\n\r\n{body}",
        body.len()
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|e| format!("could not ask the server: {e}"))?;

    let mut response = String::new();
    let _ = stream.read_to_string(&mut response);

    // `HTTP/1.1 200 OK` — the code, not the phrase, and not a substring match on the
    // whole response, which would find a "200" in a body.
    let code = response
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|code| code.parse::<u16>().ok())
        .ok_or_else(|| "the server gave no answer".to_string())?;
    if (200..300).contains(&code) {
        Ok(())
    } else {
        // The body carries FastAPI's `detail`, which is the sentence worth showing.
        let detail = response
            .split_once("\r\n\r\n")
            .and_then(|(_, body)| serde_json::from_str::<serde_json::Value>(body).ok())
            .and_then(|v| v.get("detail").and_then(|d| d.as_str()).map(str::to_string))
            .unwrap_or_else(|| format!("the server answered {code}"));
        Err(detail)
    }
}

/// Send the console somewhere, without leaving the origin it is allowed to be at.
fn go(app: &AppHandle, path: &str) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.eval(format!("location.assign({})", js_string(path)));
    }
}

fn js_string(s: &str) -> String {
    serde_json::Value::String(s.to_string()).to_string()
}

/// Build the menu and hang it on the application.
///
/// Called once the console is up, and again whenever the window is focused: the
/// ordinary way to gain a connection is to add it on the settings page and then
/// reach for the menu, and a menu built once at launch would not have it.
///
/// Takes no port. Drawing the menu only reads the settings file; it is `on_event`
/// that has to reach the server, and it is handed the port there.
pub fn install(app: &AppHandle) -> tauri::Result<()> {
    let locked = env_locked();

    let mut recent = SubmenuBuilder::new(app, "Open Recent");
    if locked {
        recent = recent.item(
            &MenuItemBuilder::with_id("locked", "LANCE_ROOT is set — it wins over these")
                .enabled(false)
                .build(app)?,
        );
    } else {
        let saved = recents();
        if saved.is_empty() {
            recent = recent.item(
                &MenuItemBuilder::with_id("no-recents", "Nothing saved yet")
                    .enabled(false)
                    .build(app)?,
            );
        }
        for connection in saved {
            // The label, then the path it resolves to, because two databases can
            // reasonably be called `lance` and the label alone would not say which.
            let text = format!("{}  —  {}", connection.label, connection.uri);
            recent = recent.item(
                &MenuItemBuilder::with_id(format!("recent:{}", connection.id), text).build(app)?,
            );
        }
    }

    let file = SubmenuBuilder::new(app, "File")
        .item(
            &MenuItemBuilder::with_id("open", "Open Database…")
                .accelerator("CmdOrCtrl+O")
                .enabled(!locked)
                .build(app)?,
        )
        .item(&recent.build()?)
        .separator()
        .item(&MenuItemBuilder::with_id("bundle", "Open a Bundle…").build(app)?)
        .separator()
        .item(&PredefinedMenuItem::close_window(app, None)?)
        .build()?;

    // Predefined throughout, so these are the system's own items and behave like
    // every other application's.
    let edit = SubmenuBuilder::new(app, "Edit")
        .item(&PredefinedMenuItem::undo(app, None)?)
        .item(&PredefinedMenuItem::redo(app, None)?)
        .separator()
        .item(&PredefinedMenuItem::cut(app, None)?)
        .item(&PredefinedMenuItem::copy(app, None)?)
        .item(&PredefinedMenuItem::paste(app, None)?)
        .item(&PredefinedMenuItem::select_all(app, None)?)
        .build()?;

    let view = SubmenuBuilder::new(app, "View")
        .item(
            &MenuItemBuilder::with_id("reload", "Reload")
                .accelerator("CmdOrCtrl+R")
                .build(app)?,
        )
        .build()?;

    let window = SubmenuBuilder::new(app, "Window")
        .item(&PredefinedMenuItem::minimize(app, None)?)
        .item(&PredefinedMenuItem::maximize(app, None)?)
        .separator()
        .item(&PredefinedMenuItem::fullscreen(app, None)?)
        .build()?;

    let help = SubmenuBuilder::new(app, "Help")
        .item(&MenuItemBuilder::with_id("guide", "LanceScope Guide").build(app)?)
        .item(&MenuItemBuilder::with_id("releases", "Release Notes").build(app)?)
        .build()?;

    let app_menu = SubmenuBuilder::new(app, "LanceScope")
        .item(&PredefinedMenuItem::about(app, None, None)?)
        .separator()
        .item(
            &MenuItemBuilder::with_id("settings", "Settings…")
                .accelerator("CmdOrCtrl+,")
                .build(app)?,
        )
        .separator()
        .item(&PredefinedMenuItem::services(app, None)?)
        .separator()
        .item(&PredefinedMenuItem::hide(app, None)?)
        .item(&PredefinedMenuItem::hide_others(app, None)?)
        .item(&PredefinedMenuItem::show_all(app, None)?)
        .separator()
        .item(&PredefinedMenuItem::quit(app, None)?)
        .build()?;

    let menu = Menu::with_items(app, &[&app_menu, &file, &edit, &view, &window, &help])?;
    app.set_menu(menu)?;
    Ok(())
}

/// What a menu item does. Everything slow runs off the menu thread, because a menu
/// that stays open while a folder picker and an HTTP round trip happen is a menu
/// that looks stuck.
pub fn on_event(app: &AppHandle, id: &str, port: u16) {
    match id {
        "settings" => go(app, "/console/settings"),
        "bundle" => go(app, "/console/bundle"),
        "guide" => go(app, "/docs/index"),
        "reload" => {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.eval("location.reload()");
            }
        }
        "releases" => {
            let _ = tauri_plugin_opener::open_url(
                "https://github.com/mrlynn/lancescope/releases",
                None::<&str>,
            );
        }
        "open" => {
            let app = app.clone();
            app.clone().dialog().file().pick_folder(move |picked| {
                let Some(path) = picked else { return };
                let Some(path) = path.as_path().map(|p| p.to_path_buf()) else {
                    return;
                };
                // The directory's own name, which is what the settings page would
                // have suggested and what `dbName()` shows in the switcher.
                let label = path
                    .file_name()
                    .map(|n| n.to_string_lossy().to_string())
                    .unwrap_or_else(|| "Database".to_string());
                let body = serde_json::json!({
                    "uri": path.to_string_lossy(),
                    "label": label,
                    "activate": true,
                })
                .to_string();
                std::thread::spawn(move || {
                    match post(port, "/settings/connections", &body) {
                        // The console is repointed in place, so this is a reload of
                        // the same page rather than a navigation.
                        Ok(()) => go(&app, "/console"),
                        Err(why) => complain(&app, &why),
                    }
                });
            });
        }
        id if id.starts_with("recent:") => {
            let connection = id.trim_start_matches("recent:").to_string();
            let app = app.clone();
            std::thread::spawn(move || {
                let path = format!("/settings/connections/{connection}/activate");
                match post(port, &path, "{}") {
                    Ok(()) => go(&app, "/console"),
                    Err(why) => complain(&app, &why),
                }
            });
        }
        _ => {}
    }
}

/// A refusal the user can read, in the window they are looking at.
///
/// Every one of these comes from the server having said no for a reason it stated —
/// a root that cannot be listed, a kiosk that refuses writes — and dropping that on
/// the floor would leave a menu item that silently did nothing.
fn complain(app: &AppHandle, why: &str) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.eval(format!("window.alert({})", js_string(why)));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const FILE: &str = r#"{
      "version": 1,
      "connections": [
        {"id": "a", "label": "Older",  "uri": "/a", "last_used": "2026-09-01T00:00:00+00:00"},
        {"id": "b", "label": "Newest", "uri": "/b", "last_used": "2026-09-05T00:00:00+00:00"},
        {"id": "c", "label": "Never",  "uri": "/c"}
      ]
    }"#;

    #[test]
    fn most_recently_used_comes_first() {
        // The one you used last is the one nearest the cursor. Timestamps are ISO
        // 8601 in UTC, which is why sorting them as text is allowed to be correct.
        let got: Vec<_> = parse_recents(FILE).into_iter().map(|r| r.id).collect();
        assert_eq!(got, ["b", "a", "c"]);
    }

    #[test]
    fn a_connection_never_opened_is_still_offered() {
        // `last_used` is absent until the first activation, and a database you saved
        // and have not opened is exactly the one you are reaching for the menu to
        // open. It sorts last, not away.
        assert!(parse_recents(FILE).iter().any(|r| r.id == "c"));
    }

    #[test]
    fn a_settings_file_it_cannot_read_is_an_empty_menu() {
        // Not an error dialog on launch. The console says so properly, on a screen
        // with room to explain; a menu is not that screen.
        assert!(parse_recents("this is not json").is_empty());
        assert!(parse_recents("{}").is_empty());
        assert!(parse_recents(r#"{"connections": "not a list"}"#).is_empty());
    }

    #[test]
    fn an_entry_missing_what_it_needs_is_skipped_not_fatal() {
        let partial =
            r#"{"connections": [{"label": "no id"}, {"id": "ok", "label": "L", "uri": "/u"}]}"#;
        let got: Vec<_> = parse_recents(partial).into_iter().map(|r| r.id).collect();
        assert_eq!(got, ["ok"]);
    }
}
