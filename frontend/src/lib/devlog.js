// Dev-only console mirror: pipe browser console output through Tauri's
// invoke bridge so it lands in ``cargo tauri dev``'s stdout. Lets an
// agent loop observe webview-side events without a manual DevTools
// attach — and gives the human a single stream when both Rust + JS
// logs are interesting.
//
// Gates:
//   1. Tauri context (no-op in regular browser).
//   2. dev-mode hostname — release Tauri's main window loads from
//      ``tauri.localhost`` via custom protocol; only dev (and review
//      windows loaded from the daemon URL) stay on ``127.0.0.1``.
//      Pairs with the Rust command's ``cfg(debug_assertions)`` body so
//      release builds are inert top-to-bottom.

export function installDevLogMirror() {
  if (typeof window === 'undefined') return;
  if (!window.__TAURI_INTERNALS__) return;
  if (window.location.hostname !== '127.0.0.1') return;

  const invoke = window.__TAURI_INTERNALS__.invoke;
  if (typeof invoke !== 'function') return;

  const wrap = (level, orig) => (...args) => {
    orig.apply(console, args);
    try {
      const message = args.map((a) => {
        if (typeof a === 'string') return a;
        try { return JSON.stringify(a); } catch { return String(a); }
      }).join(' ');
      invoke('_dev_log', { level, message });
    } catch {
      // Swallow: if the shim can't reach Rust, the local console.* still
      // ran via ``orig.apply`` above. Don't break user code.
    }
  };

  console.log = wrap('log', console.log);
  console.warn = wrap('warn', console.warn);
  console.error = wrap('error', console.error);
  console.info = wrap('info', console.info);
}
