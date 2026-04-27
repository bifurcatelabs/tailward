<script>
  import PathDiagram from './PathDiagram.svelte';
  import ProbePanel from './ProbePanel.svelte';
  import TurnMetricsPanel from './TurnMetricsPanel.svelte';
  import LlmBudgetPanel from './LlmBudgetPanel.svelte';
  import LlmProfilesPanel from './LlmProfilesPanel.svelte';

  let { ph } = $props();
</script>

<section class="view">
  <header class="hd">
    <h2>platform</h2>
    <p>
      is the platform serving me consistently — derived from your
      actual sessions plus the local model server you control.
      <strong>evidence, not verdict</strong>: this view shows variance
      and trend, it does not prove tier swaps or routing changes.
    </p>
  </header>

  <PathDiagram />
  <TurnMetricsPanel {ph} />
  <LlmProfilesPanel />
  <LlmBudgetPanel />
  <ProbePanel {ph} />
</section>

<style>
  .view {
    padding: 24px;
    max-width: 1100px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
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
</style>
