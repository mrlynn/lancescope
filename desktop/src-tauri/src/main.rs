// LanceScope, as a macOS application.
//
// The window is a WKWebView pointed at a server this process owns. That server is
// the same FastAPI application the browser console runs, frozen into an executable
// that carries its own Python — so nothing needs installing on the machine this
// lands on, and nothing depends on what the user's login shell does before a script
// gets to run.
//
// That last point is why this exists. A `.command` file is handed to the login
// shell, and anything the shell does first — an update prompt reading a character
// from stdin, for instance — happens to the launch. This process is exec'd by
// LaunchServices and has no shell in the path at all.
//
// The whole job here is lifecycle: start the server, find out which port it chose,
// wait until it answers, show a window, and make sure it dies when we do.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

/// How long to wait for the server to announce its port before giving up.
///
/// Cold, this is a Python interpreter unpacking and importing Lance and PyArrow;
/// on a slow disk with Gatekeeper checking every dylib in a freshly downloaded app
/// it is much slower than it will ever be again.
const STARTUP_TIMEOUT: Duration = Duration::from_secs(90);

/// The child, kept so it can be killed. Tauri hands `AppHandle` around freely and
/// the exit hook needs to reach this from a different thread than started it.
struct Server(Mutex<Option<Child>>);

/// Start the packaged server and read back the port it chose.
///
/// The port is the kernel's choice rather than a constant, because a fixed port is
/// a support ticket the first time somebody already has something on it. The server
/// prints `LANCESCOPE_PORT=<n>` on its first line of stdout, and this reads it.
fn start_server(app: &tauri::AppHandle) -> Result<(Child, u16), String> {
    let exe = app
        .path()
        .resolve("server/lancescope-server", tauri::path::BaseDirectory::Resource)
        .map_err(|e| format!("cannot locate the bundled server: {e}"))?;

    if !exe.exists() {
        return Err(format!(
            "the bundled server is missing from this application at {}.\n\n\
             If you are running a development build, run `make sidecar` first.",
            exe.display()
        ));
    }

    let mut child = Command::new(&exe)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        // The server exits when its parent does, which covers a crash here. Killing
        // it on the way out covers the ordinary case.
        .env("LANCESCOPE_WATCH_PARENT", "1")
        .spawn()
        .map_err(|e| format!("could not start the bundled server: {e}"))?;

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "the server produced no output to read".to_string())?;

    let (tx, rx) = std::sync::mpsc::channel::<u16>();
    std::thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines().map_while(Result::ok) {
            if let Some(port) = line.strip_prefix("LANCESCOPE_PORT=") {
                if let Ok(port) = port.trim().parse::<u16>() {
                    let _ = tx.send(port);
                }
            }
            // Everything else the server says goes to the console log, where a
            // support bundle can pick it up. Discarding it would make a server that
            // started and then failed indistinguishable from one that hung.
            println!("[server] {line}");
        }
    });

    match rx.recv_timeout(STARTUP_TIMEOUT) {
        Ok(port) => Ok((child, port)),
        Err(_) => {
            let _ = child.kill();
            Err(format!(
                "the server did not report a port within {} seconds.",
                STARTUP_TIMEOUT.as_secs()
            ))
        }
    }
}

/// Block until the server answers, so the window never opens on a connection error.
///
/// It has already told us its port by this point, which means it got as far as
/// binding one; this is the gap between binding and being ready to serve.
fn wait_until_ready(port: u16) -> bool {
    let deadline = Instant::now() + STARTUP_TIMEOUT;
    let addr = format!("127.0.0.1:{port}");
    while Instant::now() < deadline {
        if std::net::TcpStream::connect(&addr).is_ok() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(120));
    }
    false
}

/// What to show when there is no server to show. A window that fails to open tells
/// the user nothing; this tells them what happened and what to do about it.
fn failure_page(message: &str) -> String {
    format!(
        r#"<!doctype html><meta charset="utf-8">
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 14px/1.7 -apple-system, system-ui, sans-serif; margin: 0;
          display: grid; place-items: center; height: 100vh; padding: 2rem;
          background: #171513; color: #ad9e95; }}
  main {{ max-width: 46ch; }}
  h1 {{ font-size: 17px; color: #f4ebe8; margin: 0 0 .8rem; }}
  pre {{ white-space: pre-wrap; font-size: 12px; color: #847770;
         border-left: 2px solid #ff734a; padding-left: .9rem; margin-top: 1rem; }}
</style>
<main><h1>LanceScope could not start its server.</h1>
<pre>{}</pre></main>"#,
        message.replace('<', "&lt;")
    )
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(Server(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();

            match start_server(&handle) {
                Ok((child, port)) => {
                    app.state::<Server>().0.lock().unwrap().replace(child);

                    if !wait_until_ready(port) {
                        WebviewWindowBuilder::new(
                            &handle,
                            "main",
                            WebviewUrl::App(
                                format!(
                                    "data:text/html,{}",
                                    urlencoding(&failure_page(
                                        "It reported a port but never answered on it."
                                    ))
                                )
                                .parse()
                                .unwrap(),
                            ),
                        )
                        .title("LanceScope")
                        .inner_size(560.0, 420.0)
                        .build()?;
                        return Ok(());
                    }

                    // The console rather than the root: the demo is one screen and
                    // the console is the product.
                    let url = format!("http://127.0.0.1:{port}/console");
                    WebviewWindowBuilder::new(
                        &handle,
                        "main",
                        WebviewUrl::External(url.parse().unwrap()),
                    )
                    .title("LanceScope")
                    .inner_size(1280.0, 860.0)
                    .min_inner_size(760.0, 560.0)
                    // The app's own chrome, rather than a browser's, is most of what
                    // makes this feel like an application instead of a tab.
                    .title_bar_style(tauri::TitleBarStyle::Transparent)
                    .build()?;
                }
                Err(message) => {
                    WebviewWindowBuilder::new(
                        &handle,
                        "main",
                        WebviewUrl::App(
                            format!("data:text/html,{}", urlencoding(&failure_page(&message)))
                                .parse()
                                .unwrap(),
                        ),
                    )
                    .title("LanceScope")
                    .inner_size(560.0, 420.0)
                    .build()?;
                }
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing the window is quitting, on a single-window app. Without this
            // the process lingers with no way to reach it but Activity Monitor.
            if let tauri::WindowEvent::Destroyed = event {
                window.app_handle().exit(0);
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building LanceScope")
        .run(|handle, event| {
            if let tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit = event {
                // The server also watches its parent and exits on its own, but that
                // is a backstop for a crash. Leaving a web server running after the
                // window closes is a bug people discover through their fan.
                if let Some(mut child) = handle.state::<Server>().0.lock().unwrap().take() {
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
        });
}

/// Percent-encode enough of a data: URL to survive being parsed as one.
fn urlencoding(s: &str) -> String {
    s.bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                (b as char).to_string()
            }
            b' ' => "%20".to_string(),
            _ => format!("%{b:02X}"),
        })
        .collect()
}
