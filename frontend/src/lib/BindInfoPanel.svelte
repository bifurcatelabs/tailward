<script>
  // Reads /api/bind-info and surfaces the daemon's HTTP binding state.
  // Loopback bind (127.0.0.1, ::1) is the safe default; anything else
  // exposes the unauthenticated audit surface to other devices on the
  // wire and gets a prominent warning. The intent is to make the
  // 0.0.0.0 foot-gun loud rather than silent for users adopting
  // tailward in a multi-device homelab setup who reach for that
  // setting because it "just works."

  let info = $state(null);
  let loading = $state(true);
  let error = $state(null);

  async function refresh() {
    try {
      const r = await fetch('/api/bind-info');
      if (!r.ok) {
        error = `HTTP ${r.status}`;
        return;
      }
      info = await r.json();
      error = null;
    } catch (e) {
      error = String(e);
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    refresh();
  });
</script>

<section class="panel">
  <header>
    <div>
      <h3>http binding</h3>
      <p class="muted">
        where the daemon listens. loopback keeps the audit surface
        invisible to other devices; non-loopback bindings expose
        everything — there is no authentication.
      </p>
    </div>
  </header>

  {#if loading}
    <div class="muted small">loading…</div>
  {:else if error}
    <div class="err small">failed: {error}</div>
  {:else if info}
    <div class="row">
      <span class="muted small">listening on</span>
      <code class="mono">{info.http_host}:{info.http_port}</code>
      {#if info.is_loopback}
        <span class="chip chip-ok">loopback</span>
      {:else}
        <span class="chip chip-warn">non-loopback</span>
      {/if}
    </div>

    {#if info.warning}
      <div class="warning">
        <div class="warning-head">network exposure warning</div>
        <p>{info.warning}</p>
        <p class="warning-hint">
          to keep the daemon on loopback while still accessing the UI
          from another device, use an SSH or WireGuard tunnel that
          forwards <code class="mono">127.0.0.1:{info.http_port}</code>
          across the network. tailward never sees a non-loopback address;
          the tunnel does the traversal.
        </p>
      </div>
    {/if}
  {/if}
</section>

<style>
  .panel {
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px 18px;
    background: var(--surface);
  }
  header {
    margin-bottom: 12px;
  }
  h3 {
    margin: 0;
    font-size: 14px;
    font-weight: 600;
    letter-spacing: -0.01em;
  }
  .muted {
    color: var(--muted);
  }
  .small {
    font-size: 12px;
  }
  .err {
    color: var(--err);
  }
  p {
    margin: 4px 0 0;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.5;
    max-width: 720px;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 4px;
  }
  .mono {
    font-family: ui-monospace, monospace;
    font-size: 13px;
    background: var(--surface-2, rgba(255, 255, 255, 0.04));
    padding: 2px 6px;
    border-radius: 4px;
  }
  .chip {
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 999px;
    border: 1px solid;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .chip-ok {
    background: rgba(120, 180, 120, 0.10);
    color: var(--ok, #78b478);
    border-color: rgba(120, 180, 120, 0.35);
  }
  .chip-warn {
    background: rgba(232, 153, 104, 0.14);
    color: var(--accent, #e89968);
    border-color: rgba(232, 153, 104, 0.45);
  }
  .warning {
    margin-top: 12px;
    padding: 12px 14px;
    border-radius: 6px;
    background: rgba(232, 122, 122, 0.08);
    border: 1px solid rgba(232, 122, 122, 0.30);
  }
  .warning-head {
    font-size: 12px;
    font-weight: 600;
    color: var(--err, #e87a7a);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-bottom: 6px;
  }
  .warning p {
    color: var(--text);
    font-size: 13px;
    margin: 4px 0;
  }
  .warning-hint {
    color: var(--muted) !important;
    font-size: 12px !important;
  }
</style>
