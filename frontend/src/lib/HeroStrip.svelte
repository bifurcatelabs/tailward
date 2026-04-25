<script>
  import { live } from './live.svelte.js';
  import { humanize, humanizeBytes } from './format.js';
  import Sparkline from './Sparkline.svelte';
</script>

<section class="hero">
  <article class="cell">
    <div class="label">tokens out</div>
    <div class="value">{humanize(live.tokensOut)}</div>
    <div class="spark" style="color: var(--accent)">
      <Sparkline points={live.tokenOutSeries} width={140} height={28}
                 fill="rgba(232,153,104,0.10)" strokeWidth={1.6} />
    </div>
    <div class="sub">cumulative · {live.turns} turns</div>
  </article>

  <article class="cell">
    <div class="label">files touched</div>
    <div class="value">{live.files}</div>
    <div class="spark" style="color: var(--ok)">
      <Sparkline points={live.filesSeries} width={140} height={28}
                 fill="rgba(95,195,167,0.10)" strokeWidth={1.6} />
    </div>
    <div class="sub">
      {humanizeBytes(live.diffBytes)} edited
      {#if live.baseline != null && live.baseline > 0}
        · baseline {live.baseline}
      {/if}
    </div>
  </article>

  <article class="cell">
    <div class="label">cache read</div>
    <div class="value">{humanize(live.cacheRead)}</div>
    <div class="spark" style="color: var(--violet)">
      <Sparkline points={live.cacheSeries} width={140} height={28}
                 fill="rgba(150,144,248,0.08)" strokeWidth={1.6} />
    </div>
    <div class="sub">
      input {humanize(live.tokensIn)} · created {humanize(live.cacheCreate)}
    </div>
  </article>

  <article class="cell narrow">
    <div class="label">model</div>
    <div class="value model" title={live.model || ''}>
      {live.model || '—'}
    </div>
    <div class="spark-placeholder">
      <span class="dot dot-{live.violations > 0 ? 'warn' : 'ok'}"></span>
      <span class="muted">
        {#if live.violations > 0}
          {live.violations} violation{live.violations > 1 ? 's' : ''}
        {:else}
          no violations
        {/if}
      </span>
    </div>
    <div class="sub">
      rubric {live.rubricSamples} · status {live.closeStatus || 'active'}
    </div>
  </article>
</section>

<style>
  .hero {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 1px;
    background: var(--border);
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
    margin: 16px 24px 0;
  }
  @media (max-width: 1080px) {
    .hero { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  }
  @media (max-width: 540px) {
    .hero { grid-template-columns: 1fr; }
  }
  .cell {
    background: linear-gradient(180deg, var(--surface) 0%, var(--surface-2) 100%);
    padding: 18px 22px 16px;
    position: relative;
    overflow: hidden;
  }
  .cell::before {
    /* Hairline highlight on the top edge — gives the cards a sense
       of being lit from above without committing to a hard shadow. */
    content: '';
    position: absolute;
    inset: 0 0 auto 0;
    height: 1px;
    background: linear-gradient(90deg,
      transparent 0%,
      rgba(255,255,255,0.04) 50%,
      transparent 100%);
  }
  .label {
    font-size: 10px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.10em;
    font-weight: 600;
  }
  .value {
    font-family: var(--mono);
    font-size: 32px;
    font-weight: 500;
    letter-spacing: -0.02em;
    color: var(--text);
    margin-top: 6px;
    line-height: 1.05;
    font-variant-numeric: tabular-nums;
  }
  .value.model {
    font-size: 16px;
    font-weight: 500;
    color: var(--text);
    word-break: break-all;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .spark {
    margin-top: 10px;
    height: 28px;
  }
  .spark-placeholder {
    margin-top: 12px;
    display: flex;
    align-items: center;
    gap: 8px;
    height: 28px;
    font-size: 12px;
  }
  .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
  }
  .dot-ok { background: var(--ok); box-shadow: 0 0 8px rgba(94,197,179,0.5); }
  .dot-warn { background: var(--warn); box-shadow: 0 0 8px rgba(230,165,84,0.5); }
  .muted { color: var(--muted); }
  .sub {
    margin-top: 8px;
    font-size: 11px;
    color: var(--muted);
    letter-spacing: 0.01em;
  }
</style>
