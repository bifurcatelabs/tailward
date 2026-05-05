use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::CommandEvent;

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

      // Milestone 1: spawn the PyInstaller-bundled stub sidecar and
      // print its captured output to our terminal. This is the
      // experiment that validates Tauri's externalBin mechanism
      // before we wire up the real tailward daemon in milestone 2.
      // Replace once the spawn path is proven.
      let sidecar = app
        .shell()
        .sidecar("tailward-sidecar-stub")
        .expect("milestone 1: failed to construct sidecar command");

      let (mut rx, _child) = sidecar
        .spawn()
        .expect("milestone 1: failed to spawn tailward-sidecar-stub");

      tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
          match event {
            CommandEvent::Stdout(line) => {
              let s = String::from_utf8_lossy(&line);
              println!("[sidecar stdout] {}", s.trim_end());
            }
            CommandEvent::Stderr(line) => {
              let s = String::from_utf8_lossy(&line);
              eprintln!("[sidecar stderr] {}", s.trim_end());
            }
            CommandEvent::Terminated(payload) => {
              println!("[sidecar terminated] code={:?}", payload.code);
              break;
            }
            _ => {}
          }
        }
      });

      Ok(())
    })
    .run(tauri::generate_context!())
    .expect("error while running tauri application");
}
