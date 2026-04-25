<script>
  import { live } from './live.svelte.js';

  const dimensions = [
    { key: 'invariants_awareness', label: 'invariants' },
    { key: 'uncertainty_honesty', label: 'uncertainty' },
    { key: 'maintainability', label: 'maintainability' },
    { key: 'provenance', label: 'provenance' },
  ];
</script>

<aside class="rail">
  <section class="panel">
    <h3>rubric</h3>
    {#each dimensions as dim}
      {@const score = live.rubricAvgs[dim.key]}
      <div class="bar">
        <div class="bar-head">
          <span class="bar-label">{dim.label}</span>
          <span class="bar-val">{score != null ? score.toFixed(2) : '—'}</span>
        </div>
        <div class="track">
          <div
            class="fill"
            style="width: {score != null ? (score / 5 * 100).toFixed(0) : 0}%"
          ></div>
        </div>
      </div>
    {/each}
    {#if live.rubricSamples === 0}
      <p class="empty">no samples yet — rubric runs every few turns when the local LLM is reachable.</p>
    {/if}
  </section>

  <section class="panel">
    <h3>report card</h3>
    {#if live.reportRows.length > 0}
      {#each live.reportRows as row}
        <div class="report-row">
          <div class="report-head">
            <span>{row.mode_name}</span>
            <span class="report-score">{Number(row.score).toFixed(1)}<span class="muted">/5</span></span>
          </div>
          <div class="track">
            <div class="fill" style="width: {(row.score / 5 * 100).toFixed(0)}%"></div>
          </div>
          {#if row.suggestion}
            <p class="suggestion">{row.suggestion}</p>
          {/if}
        </div>
      {/each}
    {:else}
      <p class="empty">populates at session-close consolidation.</p>
    {/if}
  </section>
</aside>

<style>
  .rail {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  .panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px 18px;
  }
  h3 {
    margin: 0 0 14px;
    font-size: 11px;
    color: var(--muted);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    font-weight: 600;
  }
  .bar {
    margin-bottom: 14px;
  }
  .bar:last-of-type { margin-bottom: 0; }
  .bar-head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 4px;
    font-size: 12px;
  }
  .bar-label { color: var(--text-soft); }
  .bar-val {
    font-family: var(--mono);
    font-variant-numeric: tabular-nums;
    color: var(--text);
    font-size: 11px;
  }
  .track {
    background: var(--surface-2);
    height: 6px;
    border-radius: 3px;
    overflow: hidden;
  }
  .fill {
    height: 100%;
    background: linear-gradient(90deg, var(--accent), var(--accent-soft));
    transition: width 600ms cubic-bezier(0.22, 0.61, 0.36, 1);
  }
  .empty {
    margin: 8px 0 0;
    font-size: 11px;
    color: var(--muted);
    line-height: 1.5;
  }
  .report-row {
    margin-bottom: 14px;
  }
  .report-row:last-of-type { margin-bottom: 0; }
  .report-head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 4px;
    font-size: 12px;
    color: var(--text-soft);
  }
  .report-score {
    font-family: var(--mono);
    font-variant-numeric: tabular-nums;
    color: var(--text);
  }
  .muted { color: var(--muted); }
  .suggestion {
    margin: 6px 0 0;
    font-size: 11px;
    color: var(--muted);
    line-height: 1.5;
  }
</style>
