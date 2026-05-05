use std::time::Duration;

use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::CommandEvent;

const DAEMON_HEALTH_URL: &str = "http://127.0.0.1:7878/health";
const HEALTH_TIMEOUT_SECS: u64 = 30;
const HEALTH_POLL_INTERVAL_MS: u64 = 500;

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
    .plugin(tauri_plugin_shell::init())
    .setup(|app| {
      if cfg!(debug_assertions) {
        app.handle().plugin(
          tauri_plugin_log::Builder::default()
            .level(log::LevelFilter::Info)
            .build(),
        )?;
      }

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

        let (mut rx, _child) = match sidecar.spawn() {
          Ok(x) => x,
          Err(e) => {
            eprintln!("[tailward] failed to spawn tailward-daemon sidecar: {e}");
            return;
          }
        };

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
    .run(tauri::generate_context!())
    .expect("error while running tauri application");
}
