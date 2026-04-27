<script>
  // Educational SVG: shows the inference path from user keystrokes
  // to model output, with green ticks for what we actually measure
  // and grey ticks for the parts we deliberately don't (where the
  // signal would be dominated by ISP/CDN/transit variance, not
  // anything Anthropic controls).
  const stages = [
    { id: 'user',    label: 'you',          measured: false, note: 'keystrokes / clipboard' },
    { id: 'claude',  label: 'claude code',  measured: true,  note: 'transcript timestamps' },
    { id: 'isp',     label: 'isp',          measured: false, note: 'too noisy to interpret' },
    { id: 'edge',    label: 'cdn edge',     measured: false, note: 'too noisy to interpret' },
    { id: 'api',     label: 'anthropic api',measured: false, note: 'inferred from response' },
    { id: 'backend', label: 'model backend',measured: true,  note: 'usage block + response timing' },
  ];
</script>

<section class="diagram">
  <h3>what we measure</h3>
  <p class="lede">
    your data round-trip touches several layers we can't see directly.
    rather than synthetically pinging external endpoints (which would
    mostly capture isp + cdn variance), warden derives signal from
    timestamps and usage on the data that actually flows through your
    sessions.
  </p>

  <div class="path">
    {#each stages as s, i}
      <div class="stage" class:measured={s.measured} class:opaque={!s.measured}>
        <div class="dot"></div>
        <div class="label">{s.label}</div>
        <div class="note">{s.note}</div>
      </div>
      {#if i < stages.length - 1}
        <div class="connector" aria-hidden="true"></div>
      {/if}
    {/each}
  </div>

  <p class="legend">
    <span class="key key-measured"></span>
    measured in-band
    <span class="key key-opaque"></span>
    opaque to warden
  </p>
</section>

<style>
  .diagram {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 22px 24px;
  }
  h3 {
    margin: 0 0 6px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.10em;
    color: var(--muted);
    font-weight: 600;
  }
  .lede {
    margin: 0 0 18px;
    color: var(--text-soft);
    font-size: 13px;
    line-height: 1.55;
    max-width: 720px;
  }
  .path {
    display: flex;
    align-items: stretch;
    gap: 0;
    padding: 4px 0 12px;
    overflow-x: auto;
  }
  .stage {
    display: flex;
    flex-direction: column;
    align-items: center;
    min-width: 110px;
    text-align: center;
    flex: 1 1 auto;
  }
  .dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--muted-deep);
    margin-bottom: 8px;
  }
  .stage.measured .dot {
    background: var(--ok);
    box-shadow: 0 0 0 4px rgba(95,195,167,0.18);
  }
  .stage.opaque .dot {
    background: var(--surface-2);
    border: 1px dashed var(--muted-deep);
  }
  .label {
    font-size: 12px;
    color: var(--text);
    font-weight: 500;
    margin-bottom: 2px;
  }
  .stage.opaque .label { color: var(--muted); }
  .note {
    font-size: 10px;
    color: var(--muted);
    line-height: 1.4;
    max-width: 110px;
  }
  .connector {
    flex: 0 0 auto;
    align-self: flex-start;
    margin-top: 4px;
    width: 18px;
    height: 2px;
    background: linear-gradient(90deg, var(--border-strong), var(--border));
  }
  .legend {
    margin: 12px 0 0;
    color: var(--muted);
    font-size: 11px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .key {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    display: inline-block;
  }
  .key-measured {
    background: var(--ok);
    box-shadow: 0 0 0 3px rgba(95,195,167,0.18);
  }
  .key-opaque {
    background: var(--surface-2);
    border: 1px dashed var(--muted-deep);
    margin-left: 16px;
  }
</style>
