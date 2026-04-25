<script>
  // Placeholder. Lists the signals already on the wire that this
  // view will surface, plus the ones that need new rubric prompts /
  // backend changes. Honest about what's not built yet — no fake
  // charts.
  const present = [
    { name: 'idle gaps between user prompts', source: 'derive from user_turn timestamps' },
    { name: 'user prompt length distribution', source: 'derive from user_turn chars' },
    { name: 'destructive-action approval cadence', source: 'derive from constraint_violation status transitions' },
    { name: 'verification behavior', source: 'derive from claim verdict + ack/dismiss flow' },
  ];
  const planned = [
    { name: 'self-rubric scores', source: 'new rubric prompts targeting human-side dimensions' },
    { name: 'scope-creep contributor split', source: 'attribute scope deltas to user vs agent turns' },
    { name: 're-ask detection', source: 'embedding similarity across user prompts' },
  ];
</script>

<section class="view">
  <header class="hd">
    <h2>reflection</h2>
    <p>am I behaving — bidirectional honesty about who's drifting, the human or the agent.</p>
  </header>

  <div class="grid">
    <div class="card">
      <h3>signals already on the wire</h3>
      <ul>
        {#each present as s}
          <li>
            <strong>{s.name}</strong>
            <span class="muted">{s.source}</span>
          </li>
        {/each}
      </ul>
    </div>
    <div class="card">
      <h3>planned</h3>
      <ul>
        {#each planned as s}
          <li>
            <strong>{s.name}</strong>
            <span class="muted">{s.source}</span>
          </li>
        {/each}
      </ul>
    </div>
  </div>

  <p class="footer-note">
    nothing renders yet — this view is the destination for the user-side
    rubric work tracked in <code>project_user_as_subject.md</code>. The
    intent is honest mirrors, not flattery.
  </p>
</section>

<style>
  .view { padding: 24px; max-width: 1100px; }
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
    max-width: 560px;
    line-height: 1.55;
  }
  .grid {
    margin-top: 24px;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }
  @media (max-width: 800px) { .grid { grid-template-columns: 1fr; } }
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 18px 20px;
  }
  .card h3 {
    margin: 0 0 12px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.10em;
    color: var(--muted);
    font-weight: 600;
  }
  ul { margin: 0; padding: 0; list-style: none; }
  li {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 10px 0;
    border-top: 1px solid var(--border);
    font-size: 13px;
  }
  li:first-child { border-top: 0; padding-top: 0; }
  li:last-child { padding-bottom: 0; }
  strong { font-weight: 500; color: var(--text); }
  .muted { color: var(--muted); font-size: 12px; }
  .footer-note {
    margin-top: 28px;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.6;
    max-width: 720px;
  }
  code {
    font-family: var(--mono);
    background: var(--surface-2);
    padding: 1px 5px;
    border-radius: 3px;
    font-size: 11px;
    border: 1px solid var(--border);
  }
</style>
