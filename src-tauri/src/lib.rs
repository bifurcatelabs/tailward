use std::sync::{Arc, Mutex};
use std::time::Duration;

use tauri::{
  AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent,
  menu::{Menu, MenuItem},
  tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
};
use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::{CommandChild, CommandEvent};

const DAEMON_HEALTH_URL: &str = "http://127.0.0.1:7878/health";
const HEALTH_TIMEOUT_SECS: u64 = 30;
const HEALTH_POLL_INTERVAL_MS: u64 = 500;

/// Review surfaces that spawn dedicated windows instead of switching the
/// sidebar's view. ``session`` stays in the sidebar window because the
/// sidebar IS the live-activity surface.
const REVIEW_VIEWS: &[&str] = &["reflection", "platform", "settings"];

/// Holds the bundled daemon's child handle when we spawned it ourselves.
/// Stays ``None`` on the reuse path — the user owns that daemon's
/// lifecycle and we don't terminate it on app exit.
#[derive(Default)]
struct SpawnedDaemon(Arc<Mutex<Option<CommandChild>>>);

/// Spawn (or focus) a dedicated window for one of the review surfaces.
/// Sidebar window stays focused on live session activity; review surfaces
/// (reflection / platform / settings) open in their own larger windows
/// so chart/table area isn't fighting the narrow sidebar for space.
///
/// Focus-if-exists: a second click of the same tab brings the existing
/// window to the front instead of duplicating. The ``review-<view>``
/// label keys both the lookup and the per-window state persistence
/// handled by ``tauri-plugin-window-state``.
///
/// The spawned URL hits the daemon directly (same SPA bundle, same path
/// routing) with a ``?spawned=1`` flag the frontend uses to hide
/// TabNav, and a ``#<view>`` hash that selects the target tab.
#[tauri::command]
async fn open_review_window(
  app: AppHandle,
  view: String,
  ph: String,
  session_id: String,
) -> Result<(), String> {
  if !REVIEW_VIEWS.contains(&view.as_str()) {
    println!("[tailward] open_review_window: rejected unknown view '{view}'");
    return Err(format!("'{view}' is not a spawnable review surface"));
  }

  let label = format!("review-{view}");

  if let Some(window) = app.get_webview_window(&label) {
    println!("[tailward] open_review_window: focusing existing '{label}'");
    let _ = window.show();
    let _ = window.unminimize();
    let _ = window.set_focus();
    return Ok(());
  }

  println!("[tailward] open_review_window: spawning new '{label}' for view='{view}'");

  let url_str = format!(
    "http://127.0.0.1:7878/p/{ph}/live/{session_id}?spawned=1#{view}"
  );
  let url = url::Url::parse(&url_str).map_err(|e| e.to_string())?;

  WebviewWindowBuilder::new(&app, &label, WebviewUrl::External(url))
    .title(format!("tailward — {view}"))
    .inner_size(1100.0, 800.0)
    .min_inner_size(600.0, 480.0)
    .resizable(true)
    .build()
    .map_err(|e| {
      println!("[tailward] open_review_window: build failed: {e}");
      e.to_string()
    })?;

  println!("[tailward] open_review_window: spawned '{label}'");
  Ok(())
}

/// Dev-only sink for ``console.log/warn/error/info`` from the webview.
/// The frontend's ``devlog.js`` shim installs a console override in
/// debug-Tauri context that funnels everything here so ``cargo tauri
/// dev``'s stdout carries the SPA's logs alongside Rust's. No-op in
/// release builds (the shim doesn't install there either; this gating
/// is belt-and-braces).
#[tauri::command]
fn _dev_log(level: String, message: String) {
  #[cfg(debug_assertions)]
  println!("[webview {level}] {message}");
  #[cfg(not(debug_assertions))]
  let _ = (level, message);
}

/// Probe the daemon's ``/health`` endpoint with a tight timeout. Returns
/// true on a 2xx response; false on any error / non-2xx / timeout.
async fn daemon_is_healthy(client: &reqwest::Client) -> bool {
  match client
    .get(DAEMON_HEALTH_URL)
    .timeout(Duration::from_millis(500))
    .send()
    .await
  {
    Ok(resp) => resp.status().is_success(),
    Err(_) => false,
  }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  tauri::Builder::default()
    .invoke_handler(tauri::generate_handler![open_review_window, _dev_log])
    // Single-instance enforcement runs as the FIRST plugin so a second
    // invocation of tailward.exe is rejected before it can race the
    // bundled-daemon spawn or the health-check probe. The closure runs
    // INSIDE the existing instance — so we can safely focus its window.
    .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
      if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
      }
    }))
    .plugin(tauri_plugin_shell::init())
    // Persist window position + size across launches. Saves to a
    // platform-appropriate path on app exit, restores on next start.
    .plugin(tauri_plugin_window_state::Builder::default().build())
    .on_window_event(|window, event| {
      // Close-to-hide applies only to the main (sidebar) window — the
      // standard "background app with menu-bar / tray presence" UX.
      // Real shutdown happens via the tray menu's Quit item.
      //
      // Review windows (label ``review-<view>``) close normally so a
      // subsequent tab click triggers a fresh spawn instead of un-
      // hiding the prior window. Hiding them would shadow the
      // focus-if-exists branch since Tauri still considers a hidden
      // window "existing."
      if let WindowEvent::CloseRequested { api, .. } = event {
        if window.label() == "main" {
          let _ = window.hide();
          api.prevent_close();
        }
      }
    })
    .setup(|app| {
      // System tray (Windows notification area / macOS menu bar).
      // Click toggles main-window visibility; right-click drops a menu
      // with Show / Quit. Quit is the only path that actually exits
      // and triggers RunEvent::ExitRequested → daemon shutdown.
      let show_item = MenuItem::with_id(app, "show", "Show tailward", true, None::<&str>)?;
      let quit_item = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
      let menu = Menu::with_items(app, &[&show_item, &quit_item])?;

      let _tray = TrayIconBuilder::with_id("main-tray")
        .icon(app.default_window_icon().unwrap().clone())
        .tooltip("tailward")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
          "show" => {
            if let Some(window) = app.get_webview_window("main") {
              let _ = window.show();
              let _ = window.unminimize();
              let _ = window.set_focus();
            }
          }
          "quit" => {
            app.exit(0);
          }
          _ => {}
        })
        .on_tray_icon_event(|tray, event| {
          // Left-click toggles visibility. The standard pattern: if
          // the window is hidden, show + focus; if visible, hide.
          if let TrayIconEvent::Click {
            button: MouseButton::Left,
            button_state: MouseButtonState::Up,
            ..
          } = event
          {
            let app = tray.app_handle();
            if let Some(window) = app.get_webview_window("main") {
              let visible = window.is_visible().unwrap_or(false);
              if visible {
                let _ = window.hide();
              } else {
                let _ = window.show();
                let _ = window.unminimize();
                let _ = window.set_focus();
              }
            }
          }
        })
        .build(app)?;
      if cfg!(debug_assertions) {
        app.handle().plugin(
          tauri_plugin_log::Builder::default()
            .level(log::LevelFilter::Info)
            .build(),
        )?;
      }

      // Manage the spawned-daemon handle so the exit hook can find it.
      // Cloned ``child_slot`` goes into the spawn task; the original
      // lives in app state and is read from the run-event callback.
      let spawned = SpawnedDaemon::default();
      let child_slot = spawned.0.clone();
      app.manage(spawned);

      // Milestone 2: spawn-or-reuse the tailward daemon.
      //
      // 1. Probe ``/health``. If a daemon already responds (e.g. user
      //    started ``tailward daemon start`` from the CLI), reuse it
      //    and **don't own its lifecycle** — leave it alone on app
      //    exit.
      // 2. Otherwise, spawn the bundled PyInstaller daemon as a
      //    sidecar and poll until ``/health`` responds (or timeout).
      // 3. The webview loads ``devUrl`` (dev) / ``frontendDist``
      //    (prod) — both serve from the daemon at :7878.
      //
      // Single-instance enforcement (so the user can't double-launch
      // the app and confuse spawn-or-reuse) is a follow-up.

      let app_handle = app.handle().clone();

      tauri::async_runtime::spawn(async move {
        let client = match reqwest::Client::builder().build() {
          Ok(c) => c,
          Err(e) => {
            eprintln!("[tailward] failed to build http client: {e}");
            return;
          }
        };

        if daemon_is_healthy(&client).await {
          println!(
            "[tailward] reusing existing daemon at {DAEMON_HEALTH_URL} \
             (not spawning bundled sidecar; user owns the lifecycle)"
          );
          return;
        }

        println!("[tailward] no existing daemon detected; spawning bundled sidecar");

        let sidecar = match app_handle.shell().sidecar("tailward-daemon") {
          Ok(s) => s,
          Err(e) => {
            eprintln!("[tailward] failed to construct sidecar command: {e}");
            return;
          }
        };

        let (mut rx, child) = match sidecar.spawn() {
          Ok(x) => x,
          Err(e) => {
            eprintln!("[tailward] failed to spawn tailward-daemon sidecar: {e}");
            return;
          }
        };

        // Hand the child off to the manage()'d slot so the exit hook
        // can terminate it. We own this daemon's lifecycle from here.
        if let Ok(mut slot) = child_slot.lock() {
          *slot = Some(child);
        }

        // Drain the daemon's stdout/stderr to our terminal so a user
        // running ``cargo tauri dev`` sees backend logs interleaved.
        tauri::async_runtime::spawn(async move {
          while let Some(event) = rx.recv().await {
            match event {
              CommandEvent::Stdout(line) => {
                let s = String::from_utf8_lossy(&line);
                println!("[daemon] {}", s.trim_end());
              }
              CommandEvent::Stderr(line) => {
                let s = String::from_utf8_lossy(&line);
                eprintln!("[daemon err] {}", s.trim_end());
              }
              CommandEvent::Terminated(payload) => {
                println!("[daemon terminated] code={:?}", payload.code);
                break;
              }
              _ => {}
            }
          }
        });

        // Poll for daemon readiness. uvicorn boot is typically <2s,
        // but PyInstaller's onefile bootloader extracts to a temp dir
        // on first run and that can take longer.
        let started_at = std::time::Instant::now();
        let timeout = Duration::from_secs(HEALTH_TIMEOUT_SECS);
        loop {
          if daemon_is_healthy(&client).await {
            let elapsed_ms = started_at.elapsed().as_millis();
            println!("[tailward] daemon ready ({elapsed_ms}ms)");
            return;
          }
          if started_at.elapsed() >= timeout {
            eprintln!(
              "[tailward] daemon failed to respond on /health within {}s; \
               webview will load anyway but expect connection errors",
              HEALTH_TIMEOUT_SECS
            );
            return;
          }
          tokio::time::sleep(Duration::from_millis(HEALTH_POLL_INTERVAL_MS)).await;
        }
      });

      Ok(())
    })
    .build(tauri::generate_context!())
    .expect("error while building tauri application")
    .run(|app_handle, event| {
      // Clean-shutdown of the spawned daemon. Fires when the user
      // closes the window (or the app is otherwise asked to exit).
      // The reuse-path leaves the slot ``None`` so this is a no-op
      // when we're not the lifecycle owner.
      //
      // Cooperative path is required because the daemon is a
      // PyInstaller --onefile bundle: the spawned PID is the
      // bootloader, which exec's the actual Python interpreter as a
      // child. ``CommandChild::kill()`` (TerminateProcess on Windows /
      // SIGKILL on Unix) does not cascade to children, so killing the
      // bootloader leaves the Python child orphaned. Instead we POST
      // ``/shutdown`` so the Python process flips uvicorn's
      // ``should_exit`` flag and tears itself down — when the child
      // exits, the bootloader follows naturally. ``kill()`` is kept
      // as a fallback for the rare case where the daemon is
      // unresponsive, even though it leaves the same orphan window.
      if let RunEvent::ExitRequested { .. } = event {
        let state = app_handle.state::<SpawnedDaemon>();
        let child = state.0.lock().ok().and_then(|mut g| g.take());

        if let Some(child) = child {
          let pid = child.pid();
          println!("[tailward] requesting daemon shutdown (pid={pid})");

          let shutdown_ok = tauri::async_runtime::block_on(async {
            let client = match reqwest::Client::builder()
              .timeout(Duration::from_secs(2))
              .build()
            {
              Ok(c) => c,
              Err(_) => return false,
            };

            // Fire and forget: an EOF mid-response is normal if
            // uvicorn closes the socket before flushing. Anything
            // that arrives at all means the signal was accepted.
            let _ = client
              .post("http://127.0.0.1:7878/shutdown")
              .send()
              .await;

            // Poll /health until it stops responding (process is
            // dying) or our timeout expires. Five seconds is enough
            // for uvicorn lifespan teardown (workers stop, ledger
            // closes); if it takes longer, something is wedged.
            let started = std::time::Instant::now();
            let timeout = Duration::from_secs(5);
            loop {
              if started.elapsed() >= timeout {
                return false;
              }
              if !daemon_is_healthy(&client).await {
                return true;
              }
              tokio::time::sleep(Duration::from_millis(200)).await;
            }
          });

          if shutdown_ok {
            println!("[tailward] daemon shut down gracefully");
          } else {
            eprintln!(
              "[tailward] !!! daemon did not respond to /shutdown within \
               5s; force-killing bootloader (pid={pid}). The Python child \
               may be orphaned. Check ~/.tailward/logs/daemon.log for what \
               was holding the daemon alive — likely a worker stuck on an \
               uncancellable in-flight LLM call. Bug, not expected."
            );
            if let Err(e) = child.kill() {
              eprintln!("[tailward] failed to terminate spawned daemon: {e}");
            }
          }
        }
      }
    });
}
