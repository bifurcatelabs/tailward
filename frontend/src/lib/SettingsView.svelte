<script>
  /**
   * Settings view — local-stack transparency.
   *
   * Surfaces what the local audit stack is configured to do and what
   * it costs. Distinct from Platform (third-party-provider signals)
   * and from Reflection (user-side patterns) — this view answers
   * "what's running locally on my machine, and at what cost?"
   *
   * If the user is on cloud APIs only, this view doesn't apply; it's
   * here so that users running local inference can verify what's
   * configured without digging through config files.
   */
  import BindInfoPanel from './BindInfoPanel.svelte';
  import LlmProfilesPanel from './LlmProfilesPanel.svelte';
  import LlmBudgetPanel from './LlmBudgetPanel.svelte';
</script>

<section class="view">
  <header class="hd">
    <div class="hd-row">
      <h2>settings</h2>
    </div>
    <p>
      local-stack transparency — what's configured, what it's invoked
      for, and what it costs in tokens. If you're on cloud APIs only,
      this view doesn't apply.
    </p>
  </header>

  <BindInfoPanel />
  <LlmProfilesPanel />
  <LlmBudgetPanel />
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
  .hd-row {
    display: flex;
    align-items: center;
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
</style>
