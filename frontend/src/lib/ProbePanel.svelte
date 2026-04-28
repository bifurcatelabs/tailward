<script>
  import Sparkline from './Sparkline.svelte';
  import { humanizeDuration } from './format.js';

  let { ph } = $props();

  let probes = $state([]);
  let timer;

  async function refresh() {
    try {
      const r = await fetch('/probes/recent?target=local_llm&limit=60');
      if (!r.ok) return;
      const data = await r.json();
      probes = data.probes || [];
    } catch (e) {
      console.warn('probe fetch failed', e);
    }
  }

  $effect(() => {
    refresh();
    timer = setInterval(refresh, 5000);
    return () => clearInterval(timer);
  });

  let latest = $derived(probes.length ? probes[probes.length - 1] : null);
  let latencies = $derived(
    probes
      .map((p) => p.latency_ms)
      .filter((v) => typeof v === 'number'),
  );
  let okCount = $derived(probes.filter((p) => p.status === 'ok').length);
  let okRate = $derived(probes.length ? (okCount / probes.length) : null);
  let medianLatency = $derived.by(() => {
    if (!latencies.length) return null;
    const s = [...latencies].sort((a, b) => a - b);
    return s[Math.floor(s.length / 2)];
  });

  // Rolling 3-point average for the chart series. Single-ms variations
  // between probes are noise — the chart should communicate trend, not
  // every spike. Stats (median, last) read the raw values.
  let smoothedLatencies = $derived.by(() => {
    if (latencies.length < 3) return latencies;
    return latencies.map((_, i) => {
      const lo = Math.max(0, i - 1);
      const hi = Math.min(latencies.length, i + 2);
      const slice = latencies.slice(lo, hi);
      return slice.reduce((a, b) => a + b, 0) / slice.length;
    });
  });
  let secondsAgo = $derived.by(() => {
    if (!latest?.ts) return null;
    const t = Date.parse(latest.ts);
    if (isNaN(t)) return null;
    return Math.max(0, Math.round((Date.now() - t) / 1000));
  });
</script>

<section class="panel">
  <header>
    <div class="title">
      <h3>local llm endpoint</h3>
      <p class="muted">probed every 30s · the model server you configured</p>
    </div>
    {#if latest}
      <span class="status status-{latest.status}">
        <span class="dot"></span>
        {latest.status}
      </span>
    {/if}
  </header>

  {#if latest}
    <div class="row">
      <div class="metric">
        <span class="label">last latency</span>
        <span class="value">
          {latest.latency_ms != null ? `${latest.latency_ms} ms` : '—'}
        </span>
      </div>
      <div class="metric">
        <span class="label">median (recent)</span>
        <span class="value">
          {medianLatency != null ? `${medianLatency} ms` : '—'}
        </span>
      </div>
      <div class="metric">
        <span class="label">ok rate</span>
        <span class="value">
          {okRate != null ? `${(okRate * 100).toFixed(0)}%` : '—'}
        </span>
      </div>
      <div class="metric">
        <span class="label">last probe</span>
        <span class="value">
          {secondsAgo != null ? humanizeDuration(secondsAgo) + ' ago' : '—'}
        </span>
      </div>
    </div>

    {#if smoothedLatencies.length >= 2}
      <div class="chart" style="color: var(--ok)">
        <Sparkline points={smoothedLatencies} width={680} height={56}
                   fill="rgba(95,195,167,0.10)" strokeWidth={1.6} />
      </div>
    {/if}

    <div class="meta">
      <div>
        <span class="label">endpoint</span>
        <code>{latest.url}</code>
      </div>
      {#if latest.detail}
        <div>
          <span class="label">models loaded</span>
          <code class="thin">{latest.detail}</code>
        </div>
      {/if}
      {#if latest.error}
        <div class="err">
          <span class="label">last error</span>
          <code class="thin">{latest.error}</code>
        </div>
      {/if}
    </div>
  {:else}
    <p class="empty">no probe results yet — the worker runs every 30s.</p>
  {/if}
</section>

<style>
  .panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 20px 22px;
  }
  header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 16px;
  }
  h3 {
    margin: 0;
    font-size: 14px;
    font-weight: 600;
    color: var(--text);
  }
  .muted { color: var(--muted); font-size: 11px; margin: 4px 0 0; }
  .status {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 600;
    padding: 4px 10px;
    border-radius: 999px;
    border: 1px solid var(--border);
  }
  .status .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
  }
  .status-ok { color: var(--ok); border-color: rgba(95,195,167,0.30); background: rgba(95,195,167,0.08); }
  .status-ok .dot { background: var(--ok); box-shadow: 0 0 6px rgba(95,195,167,0.6); }
  .status-error { color: var(--err); border-color: rgba(232,122,122,0.30); background: rgba(232,122,122,0.08); }
  .status-error .dot { background: var(--err); }
  .status-timeout { color: var(--warn); border-color: rgba(230,192,84,0.30); background: rgba(230,192,84,0.08); }
  .status-timeout .dot { background: var(--warn); }

  .row {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 18px;
    margin-bottom: 14px;
  }
  @media (max-width: 720px) { .row { grid-template-columns: 1fr 1fr; } }
  .metric {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .label {
    font-size: 10px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 600;
  }
  .value {
    font-family: var(--mono);
    font-size: 18px;
    color: var(--text);
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.01em;
  }
  .chart {
    margin: 8px 0 16px;
  }
  .meta {
    display: flex;
    flex-direction: column;
    gap: 6px;
    font-size: 12px;
    border-top: 1px solid var(--border);
    padding-top: 14px;
  }
  .meta div { display: flex; gap: 10px; align-items: baseline; }
  .meta code {
    font-family: var(--mono);
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 2px 7px;
    font-size: 11px;
    color: var(--text-soft);
  }
  .meta code.thin { background: transparent; border: 0; padding: 0; color: var(--muted); }
  .meta .err code { color: var(--err); border-color: rgba(232,122,122,0.30); }
  .empty {
    margin: 0;
    color: var(--muted);
    font-size: 12px;
  }
</style>
