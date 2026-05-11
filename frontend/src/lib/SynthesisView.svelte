<script>
  // Dedicated synthesis surface. Houses the snapshot list +
  // synthesize-now trigger + per-snapshot detail. Spawned in its own
  // window via #30 from the main window's tab click.

  import SnapshotsPanel from './SnapshotsPanel.svelte';

  let { ph, sessionId } = $props();
</script>

<section class="view">
  <header class="hd">
    <h2>synthesis</h2>
    <p>
      session-state captures — periodic, on-demand, and threshold-
      triggered comprehensive synthesis runs against the local LLM.
      Each snapshot is a point-in-time summary of the conversation
      window for handoff or memory-update use.
    </p>
  </header>

  {#if ph && sessionId}
    <SnapshotsPanel {ph} {sessionId} />
  {:else}
    <p class="empty">open a session to see its synthesis stream.</p>
  {/if}
</section>

<style>
  .view {
    padding: 24px;
    max-width: 1100px;
    margin: 0 auto;
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
  .empty {
    color: var(--muted);
    font-size: 13px;
  }
</style>
