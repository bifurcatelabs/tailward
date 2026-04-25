<script>
  import { live } from './live.svelte.js';
  import { humanize, humanizeBytes } from './format.js';
  import Stat from './Stat.svelte';
</script>

<section class="strip">
  <Stat label="turns" value={live.turns} />
  <Stat label="files" value={live.files} />
  <Stat label="diff" value={humanizeBytes(live.diffBytes)} />
  <Stat
    label="baseline"
    value={live.baseline != null ? live.baseline : '—'}
    title={live.threshold != null ? `creep threshold ${live.threshold}` : undefined}
  />
  <span class="div"></span>
  <Stat label="tokens in" value={humanize(live.tokensIn)} title="cumulative input tokens" />
  <Stat label="tokens out" value={humanize(live.tokensOut)} title="cumulative output tokens" />
  <Stat
    label="cache read"
    value={humanize(live.cacheRead)}
    title="cumulative cache_read_input_tokens"
    accent="cache"
  />
  <span class="div"></span>
  <Stat
    label="violations"
    value={live.violations}
    accent={live.violations > 0 ? 'warn' : null}
  />
  <Stat label="rubric" value={live.rubricSamples} title="rubric samples" />
  <Stat
    label="status"
    value={live.closeStatus || 'active'}
    accent={live.closeStatus === 'closed' ? 'muted' : 'ok'}
  />
</section>

<style>
  .strip {
    display: flex;
    align-items: center;
    gap: 22px;
    padding: 14px 24px;
    border-bottom: 1px solid var(--border);
    background: var(--bg);
    overflow-x: auto;
    scrollbar-width: thin;
  }
  .div {
    width: 1px;
    align-self: stretch;
    background: var(--border);
    margin: 0 4px;
  }
</style>
