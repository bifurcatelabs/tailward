<script>
  // Custom title bar — rendered only in Tauri context where the
  // native window chrome has been suppressed via decorations:false.
  // The full bar (minus the controls) is a drag region; window
  // controls invoke Rust commands that operate on the bar's
  // current ``WebviewWindow``.
  //
  // Close routes through the global ``WindowEvent::CloseRequested``
  // handler — main window hides, review windows destroy.

  let { title = 'tailward' } = $props();

  const isTauri = typeof window !== 'undefined'
    && window.__TAURI_INTERNALS__ !== undefined;

  async function invoke(cmd) {
    if (!isTauri) return;
    try {
      await window.__TAURI_INTERNALS__.invoke(cmd);
    } catch (e) {
      console.error('[tailward] window control failed:', cmd, e);
    }
  }
</script>

<div class="titlebar" data-tauri-drag-region>
  <span class="title" data-tauri-drag-region>{title}</span>
  <div class="controls">
    <button
      type="button"
      class="ctrl"
      onclick={() => invoke('window_minimize')}
      title="minimize"
      aria-label="minimize"
    >
      <svg viewBox="0 0 12 12" aria-hidden="true">
        <line x1="2" y1="6.5" x2="10" y2="6.5" stroke="currentColor" stroke-width="1" />
      </svg>
    </button>
    <button
      type="button"
      class="ctrl"
      onclick={() => invoke('window_toggle_maximize')}
      title="maximize"
      aria-label="maximize"
    >
      <svg viewBox="0 0 12 12" aria-hidden="true">
        <rect x="2.5" y="2.5" width="7" height="7" stroke="currentColor" stroke-width="1" fill="none" />
      </svg>
    </button>
    <button
      type="button"
      class="ctrl close"
      onclick={() => invoke('window_close')}
      title="close"
      aria-label="close"
    >
      <svg viewBox="0 0 12 12" aria-hidden="true">
        <line x1="3" y1="3" x2="9" y2="9" stroke="currentColor" stroke-width="1" />
        <line x1="9" y1="3" x2="3" y2="9" stroke="currentColor" stroke-width="1" />
      </svg>
    </button>
  </div>
</div>

<style>
  .titlebar {
    display: flex;
    align-items: stretch;
    height: 30px;
    background: var(--bg);
    border-bottom: 1px solid var(--border);
    user-select: none;
    /* Sit above page content so the picker / search panels overlay
       without bleeding through. */
    position: relative;
    z-index: 200;
  }
  .title {
    flex: 1;
    display: flex;
    align-items: center;
    padding: 0 12px;
    font-size: 11px;
    color: var(--muted);
    letter-spacing: 0.02em;
    cursor: default;
  }
  .controls {
    display: flex;
  }
  .ctrl {
    width: 44px;
    height: 30px;
    background: transparent;
    border: 0;
    color: var(--muted);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: background 120ms, color 120ms;
    font-family: inherit;
  }
  .ctrl svg {
    width: 12px;
    height: 12px;
  }
  .ctrl:hover {
    background: var(--surface-2);
    color: var(--text);
  }
  .ctrl.close:hover {
    background: var(--err);
    color: white;
  }
</style>
