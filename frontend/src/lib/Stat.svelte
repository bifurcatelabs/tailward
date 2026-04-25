<script>
  let { label, value, accent = null, title = undefined } = $props();
</script>

<div class="stat" class:accent-warn={accent === 'warn'}
     class:accent-ok={accent === 'ok'}
     class:accent-muted={accent === 'muted'}
     class:accent-cache={accent === 'cache'}
     {title}>
  <span class="label">{label}</span>
  <span class="value">{value}</span>
</div>

<style>
  .stat {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 70px;
  }
  .label {
    font-size: 10px;
    color: var(--muted);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    font-weight: 500;
  }
  .value {
    font-family: var(--mono);
    font-size: 14px;
    color: var(--text);
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.01em;
  }
  .accent-warn .value { color: var(--warn); }
  .accent-ok .value { color: var(--ok); }
  .accent-muted .value { color: var(--muted); }
  .accent-cache .value { color: var(--accent-soft); }
</style>
