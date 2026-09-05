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
// wait until it answers, show a window, and make sure it dies when we do — and say
// what is happening while all that takes its time, because on a first run it takes
// most of a minute and used to take it behind a dock icon and no window at all.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::collections::VecDeque;
use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

mod menu;

/// How long to wait for the server to announce its port.
///
/// Cold, this is a frozen Python unpacking and importing Lance and PyArrow; on a
/// first run, with Gatekeeper checking every dylib in a freshly downloaded app, it
/// is much slower than it will ever be again.
const PORT_TIMEOUT: Duration = Duration::from_secs(90);

/// And how long to wait for it to answer on that port.
///
/// Its own budget, and a much smaller one. By the time the port is printed the slow
/// work is done and uvicorn is binding; if that has not happened in twenty seconds
/// it is not going to. These were one constant, which meant a server that printed a
/// port and then died took three minutes to say so.
const READY_TIMEOUT: Duration = Duration::from_secs(20);

/// How much of the server's output to keep for a failure page.
///
/// A page that says "it never answered" while the traceback explaining why sits in
/// a stdout nobody is reading is the generic error toast in a different costume.
const TAIL_LINES: usize = 40;

/// The colour behind the window, including the strip the transparent title bar
/// leaves above the page.
///
/// Nothing set this, so that strip was whatever the platform chose and did not
/// match the page under it. This is `--ink` from `web/app/globals.css`, the light
/// canvas — light rather than the system appearance, because the console's dark
/// theme is opt-in and a machine set to dark still renders it light until somebody
/// says otherwise. When the theme moves out of the browser's storage and into the
/// settings file, this reads that instead.
const STAGE: tauri::window::Color = tauri::window::Color(0xfa, 0xf7, 0xf5, 0xff);

/// The child, kept so it can be killed. Tauri hands `AppHandle` around freely and
/// the exit hook needs to reach this from a different thread than started it.
struct Server(Mutex<Option<Child>>);

/// The port the server chose, once it has. `None` until then — which is exactly the
/// window in which there is no menu to press either, since the menu is built from
/// this.
struct Port(Mutex<Option<u16>>);

/// The last few lines the server said, for the page that has to explain itself.
type Tail = Arc<Mutex<VecDeque<String>>>;

/// How to launch the server.
///
/// Normally the sidecar inside the bundle. In a debug build `LANCESCOPE_SERVER_CMD`
/// replaces it, which is the difference between a twenty-second edit-run loop and
/// one that waits for PyInstaller every time:
///
///     LANCESCOPE_SERVER_CMD="../../.venv/bin/python ../../server/standalone.py" cargo run
///
/// Debug only, deliberately. A shipped app that takes its server from the
/// environment is a shipped app that can be told to run something else.
fn server_command(app: &tauri::AppHandle) -> Result<Command, String> {
    #[cfg(debug_assertions)]
    if let Ok(spec) = std::env::var("LANCESCOPE_SERVER_CMD") {
        let mut parts = spec.split_whitespace();
        let program = parts
            .next()
            .ok_or_else(|| "LANCESCOPE_SERVER_CMD is empty".to_string())?;
        let mut cmd = Command::new(program);
        cmd.args(parts);
        return Ok(cmd);
    }

    let exe = app
        .path()
        .resolve(
            "server/lancescope-server",
            tauri::path::BaseDirectory::Resource,
        )
        .map_err(|e| format!("cannot locate the bundled server: {e}"))?;

    if !exe.exists() {
        return Err(format!(
            "the bundled server is missing from this application at {}.\n\n\
             If you are running a development build, run `make sidecar` first.",
            exe.display()
        ));
    }
    Ok(Command::new(&exe))
}

/// Start the packaged server and read back the port it chose.
///
/// The port is the kernel's choice rather than a constant, because a fixed port is
/// a support ticket the first time somebody already has something on it. The server
/// prints `LANCESCOPE_PORT=<n>` when it has one, and `LANCESCOPE_STAGE=<id>|<text>`
/// on the way there — the second because the first arrives at the *end* of the wait,
/// so a window mirroring only the port has nothing to show during the part that
/// takes the time.
fn start_server(
    app: &tauri::AppHandle,
    tail: Tail,
    on_stage: impl Fn(&str) + Send + 'static,
) -> Result<(Child, u16), String> {
    let mut child = server_command(app)?
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        // The server exits when its parent does, which covers a crash here. Killing
        // it on the way out covers the ordinary case. It also tells the server there
        // is a parent listening, which is what turns the stage lines on.
        .env("LANCESCOPE_WATCH_PARENT", "1")
        .spawn()
        .map_err(|e| format!("could not start the bundled server: {e}"))?;

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "the server produced no output to read".to_string())?;

    let (tx, rx) = std::sync::mpsc::channel::<u16>();
    let keep = tail.clone();
    std::thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines().map_while(Result::ok) {
            if let Some(port) = line.strip_prefix("LANCESCOPE_PORT=") {
                if let Ok(port) = port.trim().parse::<u16>() {
                    let _ = tx.send(port);
                }
            } else if let Some(rest) = line.strip_prefix("LANCESCOPE_STAGE=") {
                // `<id>|<text>`. The text is shown; the id is there so a reader that
                // wants to act on a particular stage can, and so a shell that does
                // not recognise one can still show its sentence.
                let text = rest.split_once('|').map(|(_, t)| t).unwrap_or(rest);
                on_stage(text);
            }
            // Everything else is kept, both for the console log and for the failure
            // page — which is the only place a user will ever see it.
            {
                let mut keep = keep.lock().unwrap();
                if keep.len() == TAIL_LINES {
                    keep.pop_front();
                }
                keep.push_back(line.clone());
            }
            println!("[server] {line}");
        }
    });

    // Polled rather than waited on in one go, so a server that dies during its
    // import is reported when it dies rather than ninety seconds later. That is the
    // common failure — a missing dylib, a bad `.cred`, a half-copied install — and
    // it used to look exactly like a hang.
    let deadline = Instant::now() + PORT_TIMEOUT;
    loop {
        match rx.recv_timeout(Duration::from_millis(150)) {
            Ok(port) => return Ok((child, port)),
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
        }
        if let Ok(Some(status)) = child.try_wait() {
            return Err(format!(
                "the server stopped before it was ready ({status})."
            ));
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            return Err(format!(
                "the server did not report a port within {} seconds.",
                PORT_TIMEOUT.as_secs()
            ));
        }
    }

    // The reader thread ended, which on a child that exits happens before the poll
    // above notices — closing stdout is the first thing a dying process does. Ask
    // for the status anyway rather than reporting the symptom: "stopped, exit 3" is
    // something to act on, "closed its output" is something to wonder about.
    let status = child.wait().ok();
    Err(match status {
        Some(status) => format!("the server stopped before it was ready ({status})."),
        None => "the server closed its output without reporting a port.".to_string(),
    })
}

/// Block until the server answers, so the window never opens on a connection error.
///
/// It has already told us its port by this point, which means it got as far as
/// binding one; this is the gap between binding and being ready to serve. That gap
/// is normally well under a second, which is why the first second is polled far
/// more often than the old fixed 120ms — that interval was visible on every launch.
fn wait_until_ready(port: u16) -> bool {
    let start = Instant::now();
    let addr = format!("127.0.0.1:{port}");
    while start.elapsed() < READY_TIMEOUT {
        if std::net::TcpStream::connect(&addr).is_ok() {
            return true;
        }
        let interval = if start.elapsed() < Duration::from_secs(1) {
            25
        } else {
            120
        };
        std::thread::sleep(Duration::from_millis(interval));
    }
    false
}

/// What to show when there is no server to show. A window that fails to open tells
/// the user nothing; this tells them what happened, and what the server said on its
/// way out.
fn failure_page(message: &str, tail: &Tail) -> String {
    let said = tail
        .lock()
        .unwrap()
        .iter()
        .cloned()
        .collect::<Vec<_>>()
        .join("\n");
    let said = if said.trim().is_empty() {
        "It printed nothing at all.".to_string()
    } else {
        said
    };
    format!(
        r#"<!doctype html><meta charset="utf-8">
<style>
  :root {{ color-scheme: light dark;
           --ink: #faf7f5; --bright: #1a1614; --haze: #615850; --dim: #75695f;
           --accent: #b0350e; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --ink: #171513; --bright: #f4ebe8; --haze: #a3958c; --dim: #847770;
             --accent: #ff734a; }}
  }}
  body {{ font: 14px/1.7 -apple-system, system-ui, sans-serif; margin: 0;
          padding: 2rem; background: var(--ink); color: var(--haze); }}
  main {{ max-width: 62ch; margin: 0 auto; }}
  h1 {{ font-size: 17px; color: var(--bright); margin: 0 0 .8rem; }}
  p {{ margin: 0 0 1rem; }}
  h2 {{ font-size: 11px; letter-spacing: .14em; text-transform: uppercase;
        color: var(--dim); margin: 1.6rem 0 .5rem; font-weight: 600; }}
  pre {{ white-space: pre-wrap; font-size: 11px; color: var(--dim);
         border-left: 2px solid var(--accent); padding-left: .9rem; margin: 0;
         max-height: 46vh; overflow: auto; }}
</style>
<main>
  <h1>LanceScope could not start its server.</h1>
  <p>{}</p>
  <h2>What the server said</h2>
  <pre>{}</pre>
</main>"#,
        escape(message),
        escape(&said)
    )
}

fn escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

/// Everything that takes time, off the thread that has to stay responsive.
///
/// This used to run inside `setup()`, which is why there was no window for up to
/// three minutes: `setup()` returns before Tauri starts its event loop, so nothing
/// it builds can paint until it finishes.
fn boot(handle: tauri::AppHandle) {
    let tail: Tail = Arc::new(Mutex::new(VecDeque::with_capacity(TAIL_LINES)));

    let for_stage = handle.clone();
    let started = start_server(&handle, tail.clone(), move |text| {
        if let Some(splash) = for_stage.get_webview_window("splash") {
            // A string literal in a script, so it is escaped as one. A stage line is
            // written by us, but it arrives over a pipe and the pipe is the sort of
            // thing that gets a filename in it one day.
            let js = format!("window.stage && window.stage({})", json_string(text));
            let _ = splash.eval(js);
        }
    });

    match started.and_then(|(child, port)| {
        handle.state::<Server>().0.lock().unwrap().replace(child);
        if wait_until_ready(port) {
            Ok(port)
        } else {
            Err("It reported a port but never answered on it.".to_string())
        }
    }) {
        Ok(port) => {
            let _ = handle.clone().run_on_main_thread(move || {
                // The console rather than the root: the demo is one screen and the
                // console is the product.
                let url = format!("http://127.0.0.1:{port}/console");
                let built = WebviewWindowBuilder::new(
                    &handle,
                    "main",
                    WebviewUrl::External(url.parse().unwrap()),
                )
                .title("LanceScope")
                .inner_size(1280.0, 860.0)
                .min_inner_size(760.0, 560.0)
                // The app's own chrome, rather than a browser's, is most of what
                // makes this feel like an application instead of a tab.
                //
                // `Transparent` rather than `Overlay`, deliberately. Overlay is what
                // puts HTML under the traffic lights, and it costs a title bar whose
                // height varies by OS version, a drag region we would have to build,
                // and a window that cannot be dragged while unfocused (tauri#4316).
                // None of that buys anything the colour below does not.
                .title_bar_style(tauri::TitleBarStyle::Transparent)
                // The window already wears its name in the page's own header, so the
                // system title is a second one saying the same thing.
                .hidden_title(true)
                .background_color(STAGE)
                // The window is this server's console and nothing else.
                //
                // Without this, a link to the guide or to LanceDB navigates the app
                // window away from the console with no back button to return it —
                // present today, and the reason for the `opener` plugin. It is also
                // what will keep `http://127.0.0.1:*` honest when this window is
                // eventually granted a command: the pattern has to be a wildcard
                // because the port is the kernel's, so the guarantee has to come
                // from here instead.
                .on_navigation(move |url| {
                    let ours = url.scheme() == "http"
                        && url.host_str() == Some("127.0.0.1")
                        && url.port() == Some(port);
                    if !ours {
                        let _ = tauri_plugin_opener::open_url(url.as_str(), None::<&str>);
                    }
                    ours
                })
                .build();

                // Only once the real window exists, so there is never a moment with
                // no window on screen — and never a moment with none at all, which
                // the quit-on-close rule would read as the user leaving.
                if built.is_ok() {
                    if let Some(splash) = handle.get_webview_window("splash") {
                        let _ = splash.close();
                    }
                    // Now rather than in `setup()`: half the menu is Open Recent,
                    // and every item in it needs a port to activate through. Until
                    // this point macOS shows Tauri's default menu, which carries
                    // Quit — so there is never a moment without one.
                    handle.state::<Port>().0.lock().unwrap().replace(port);
                    let _ = menu::install(&handle);
                }
            });
        }
        Err(message) => {
            // On stdout as well as on screen. The window is for the person; this is
            // for whoever is later handed a log and asked what happened.
            println!("[shell] startup failed: {message}");
            let page = failure_page(&message, &tail);
            let _ = handle.clone().run_on_main_thread(move || {
                // The splash becomes the failure surface rather than being replaced
                // by one. A window that disappears and is followed by another reads
                // as a crash even when it is an explanation.
                if let Some(splash) = handle.get_webview_window("splash") {
                    let url = format!("data:text/html,{}", urlencoding(&page));
                    if let Ok(url) = url.parse() {
                        let _ = splash.set_size(tauri::LogicalSize::new(620.0, 520.0));
                        let _ = splash.set_resizable(true);
                        let _ = splash.center();
                        let _ = splash.navigate(url);
                        return;
                    }
                }
                let _ = WebviewWindowBuilder::new(
                    &handle,
                    "failure",
                    WebviewUrl::App(
                        format!("data:text/html,{}", urlencoding(&page))
                            .parse()
                            .unwrap(),
                    ),
                )
                .title("LanceScope")
                .inner_size(620.0, 520.0)
                .build();
            });
        }
    }
}

fn main() {
    tauri::Builder::default()
        // First, and it has to be: the point of this plugin is to decide whether
        // this process should exist at all, and it can only do that before anything
        // else has started. A second launch used to spawn a second sidecar on a
        // second port, leaving two servers and two windows over one database.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            // The window the user already has, brought forward — which is what they
            // meant by double-clicking a second time.
            for label in ["main", "splash"] {
                if let Some(window) = app.get_webview_window(label) {
                    let _ = window.unminimize();
                    let _ = window.show();
                    let _ = window.set_focus();
                    return;
                }
            }
        }))
        // Size and position, remembered. `splash` is skipped because it is 480x300
        // and transient, and restoring the console's geometry onto it — or worse,
        // saving its geometry over the console's — is how a workbench comes back
        // the size of a progress dialog.
        .plugin(
            tauri_plugin_window_state::Builder::default()
                .with_denylist(&["splash", "failure"])
                .build(),
        )
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(Server(Mutex::new(None)))
        .manage(Port(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();

            // Before anything slow, and synchronously: it is a local page out of the
            // bundle, it costs nothing, and it is the only thing standing between a
            // cold launch and a dock icon that bounces for a minute.
            WebviewWindowBuilder::new(&handle, "splash", WebviewUrl::App("splash.html".into()))
                .title("LanceScope")
                .inner_size(480.0, 300.0)
                .resizable(false)
                .center()
                .background_color(STAGE)
                // Decorations kept. A splash with no way to close it is a splash you
                // have to reach Activity Monitor to escape, and the case this window
                // exists for is the one where something is wrong.
                .title_bar_style(tauri::TitleBarStyle::Transparent)
                .build()?;

            std::thread::spawn(move || boot(handle));
            Ok(())
        })
        .on_menu_event(|app, event| {
            let port = *app.state::<Port>().0.lock().unwrap();
            if let Some(port) = port {
                menu::on_event(app, event.id().0.as_str(), port);
            }
        })
        .on_window_event(|window, event| {
            // The ordinary way to gain a connection is to add it on the settings
            // page and then reach for the menu, so the menu is rebuilt when the
            // window comes forward rather than only at launch. It is one read of a
            // two-kilobyte file.
            if let tauri::WindowEvent::Focused(true) = event {
                let app = window.app_handle();
                if app.state::<Port>().0.lock().unwrap().is_some() {
                    let _ = menu::install(app);
                }
            }
            if let tauri::WindowEvent::Destroyed = event {
                // Closing the window is quitting, on a single-window app. Without
                // this the process lingers with no way to reach it but Activity
                // Monitor.
                //
                // Asked as "is there anything left" rather than by label, because the
                // splash closes twice over: once because the user gave up waiting,
                // which should quit, and once because the console replaced it, which
                // must not. The difference between them is whether a main window
                // exists by then, and that is exactly what this reads.
                let handle = window.app_handle();
                if handle.get_webview_window("main").is_none()
                    && window.label() != "failure"
                    && handle.get_webview_window("failure").is_none()
                {
                    handle.exit(0);
                }
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

/// A JavaScript string literal, quotes and all.
fn json_string(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
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
