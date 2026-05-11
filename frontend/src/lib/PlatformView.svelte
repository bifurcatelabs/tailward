<script>
  import HeroStrip from './HeroStrip.svelte';
  import PathDiagram from './PathDiagram.svelte';
  import ProbePanel from './ProbePanel.svelte';
  import TurnMetricsPanel from './TurnMetricsPanel.svelte';
  import StopReasonsPanel from './StopReasonsPanel.svelte';
  import ClaimVerificationPanel from './ClaimVerificationPanel.svelte';
  import ProjectQuickPicker from './ProjectQuickPicker.svelte';
  import Rail from './Rail.svelte';

  let { ph } = $props();
</script>

<section class="view">
  <header class="hd">
    <div class="hd-row">
      <h2>platform</h2>
      <ProjectQuickPicker {ph} />
    </div>
    <p>
      third-party-provider-facing signals — Anthropic-emitted metrics
      from real sessions, plus a probe of the local LLM stack that
      audits them. <strong>variance and trend</strong> — read it how
      you want.
    </p>
  </header>

  <HeroStrip />
  <PathDiagram />
  <TurnMetricsPanel {ph} />
  <StopReasonsPanel {ph} />
  <ClaimVerificationPanel {ph} />
  <ProbePanel {ph} />
  <!-- Agent-output audits: LLM-judged rubric + session-close
       8-mode report card. Moved from session view's right rail
       so the live page can be filter-and-arc heavy and the audit
       signals live next to the rest of the inference stack. -->
  <Rail />
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
  .hd strong {
    color: var(--text-soft);
    font-weight: 600;
  }
</style>
