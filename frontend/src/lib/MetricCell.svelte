<script>
  import Sparkline from './Sparkline.svelte';
  import { humanize } from './format.js';

  let {
    label,
    unit = '',
    series = [],
    color = 'var(--accent)',
    fill = 'transparent',
    formatValue,
    hint,
  } = $props();

  let latest = $derived(series.length ? series[series.length - 1] : null);
  let med = $derived.by(() => {
    if (!series.length) return null;
    const s = [...series].sort((a, b) => a - b);
    return s[Math.floor(s.length / 2)];
  });
</script>

<article class="cell">
  <div class="head">
    <span class="label">{label}</span>
    {#if hint}<span class="hint">{hint}</span>{/if}
  </div>
  <div class="row">
    <span class="value">
      {formatValue
        ? formatValue(latest)
        : (latest != null ? humanize(latest) : '—')}
      {#if unit && latest != null && !formatValue}
        <span class="unit">{unit}</span>
      {/if}
    </span>
    <span class="median">
      {#if med != null}
        med {formatValue ? formatValue(med) : humanize(med)}{unit && !formatValue ? ' ' + unit : ''}
      {/if}
    </span>
  </div>
  <div class="spark" style:color={color}>
    <Sparkline points={series} width={300} height={36} fill={fill} strokeWidth={1.6} />
  </div>
</article>

<style>
  .cell {
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
  }
  .head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 8px;
  }
  .label {
    font-size: 10px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 600;
  }
  .hint {
    font-size: 10px;
    color: var(--muted-deep);
    text-align: right;
    max-width: 60%;
  }
  .row {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
  }
  .value {
    font-family: var(--mono);
    font-size: 22px;
    color: var(--text);
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.01em;
  }
  .unit {
    font-size: 12px;
    color: var(--muted);
    margin-left: 4px;
    font-weight: 400;
  }
  .median {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--muted);
  }
  .spark { margin-top: 6px; }
</style>
