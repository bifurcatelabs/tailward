<script>
  // Placeholder. Renders the probe targets the worker *will* monitor
  // once it lands, with a clearly-disabled "no data yet" state per
  // target. This avoids fake charts while making the intended shape
  // visible.
  const targets = [
    {
      name: 'api.anthropic.com',
      role: 'inference path',
      check: 'TLS handshake + /v1/messages probe (no model call)',
    },
    {
      name: 'status.anthropic.com',
      role: 'platform status',
      check: 'public status JSON',
    },
    {
      name: 'local LLM endpoint',
      role: 'rubric / consolidator backend',
      check: 'OpenAI-compatible /v1/models',
    },
  ];
</script>

<section class="view">
  <header class="hd">
    <h2>platform</h2>
    <p>
      is the platform serving me consistently — latency, status, and
      reachability for the inference path. <strong>evidence, not
      verdict</strong>: this view shows variance and trend, it does not
      prove tier swaps or routing changes.
    </p>
  </header>

  <div class="targets">
    {#each targets as t}
      <article class="target">
        <header>
          <h3>{t.name}</h3>
          <span class="status-dot status-pending" title="probe worker not yet running"></span>
        </header>
        <p class="role">{t.role}</p>
        <p class="check muted">probe: {t.check}</p>
        <div class="placeholder">
          <span>no data yet</span>
        </div>
      </article>
    {/each}
  </div>

  <p class="footer-note">
    the probe worker is the next backend commit on this branch. it'll
    schedule probes on a configurable cadence, persist latency + outage
    rows to a new <code>probes</code> table, and stream them through
    the existing LiveBus into this view.
  </p>
</section>

<style>
  .view { padding: 24px; max-width: 1100px; }
  .hd h2 {
    margin: 0;
    font-size: 18px;
    font-weight: 600;
    letter-spacing: -0.01em;
  }
  .hd p {
    margin: 6px 0 0;
    color: var(--muted);
    font-size: 13px;
    max-width: 720px;
    line-height: 1.55;
  }
  .hd strong {
    color: var(--text-soft);
    font-weight: 600;
  }
  .targets {
    margin-top: 24px;
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 16px;
  }
  @media (max-width: 1000px) { .targets { grid-template-columns: 1fr 1fr; } }
  @media (max-width: 720px) { .targets { grid-template-columns: 1fr; } }
  .target {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px 0;
    display: flex;
    flex-direction: column;
  }
  .target header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 8px;
  }
  .target h3 {
    margin: 0;
    font-size: 14px;
    font-family: var(--mono);
    color: var(--text);
    font-weight: 500;
    letter-spacing: 0;
  }
  .status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .status-pending {
    background: var(--muted-deep);
    box-shadow: 0 0 0 3px rgba(77,84,98,0.15);
  }
  .role {
    margin: 0;
    font-size: 12px;
    color: var(--text-soft);
    text-transform: lowercase;
    letter-spacing: 0.02em;
  }
  .check {
    margin: 4px 0 14px;
    font-size: 11px;
  }
  .muted { color: var(--muted); }
  .placeholder {
    margin: 0 -18px;
    padding: 28px;
    border-top: 1px solid var(--border);
    background:
      repeating-linear-gradient(
        45deg,
        var(--surface) 0px,
        var(--surface) 8px,
        var(--surface-2) 8px,
        var(--surface-2) 16px
      );
    color: var(--muted-deep);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    text-align: center;
  }
  .footer-note {
    margin-top: 28px;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.6;
    max-width: 720px;
  }
  code {
    font-family: var(--mono);
    background: var(--surface-2);
    padding: 1px 5px;
    border-radius: 3px;
    font-size: 11px;
    border: 1px solid var(--border);
  }
</style>
