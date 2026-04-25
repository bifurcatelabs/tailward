<script>
  // Top-tab navigation. The four trust questions become the four
  // surfaces; copy here is intentionally first-person so the tabs
  // read as "what do I want to know" rather than feature labels.
  let { view, setView } = $props();

  const tabs = [
    { id: 'session',    label: 'session',    sub: 'is the agent behaving' },
    { id: 'reflection', label: 'reflection', sub: 'am I behaving' },
    { id: 'platform',   label: 'platform',   sub: 'is the platform serving me' },
  ];
</script>

<nav class="tabs">
  {#each tabs as t}
    <button
      type="button"
      class:active={view === t.id}
      onclick={() => setView(t.id)}
    >
      <span class="label">{t.label}</span>
      <span class="sub">{t.sub}</span>
    </button>
  {/each}
</nav>

<style>
  .tabs {
    display: flex;
    align-items: stretch;
    gap: 0;
    padding: 0 24px;
    border-bottom: 1px solid var(--border);
    background: var(--bg);
  }
  button {
    background: transparent;
    border: 0;
    border-bottom: 2px solid transparent;
    color: var(--muted);
    padding: 14px 20px 12px;
    cursor: pointer;
    text-align: left;
    display: flex;
    flex-direction: column;
    gap: 2px;
    transition: color 140ms ease, border-color 140ms ease;
  }
  button:hover {
    color: var(--text-soft);
  }
  button.active {
    color: var(--text);
    border-bottom-color: var(--accent);
  }
  .label {
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.01em;
    text-transform: lowercase;
  }
  .sub {
    font-size: 10px;
    color: var(--muted-deep);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 500;
  }
  button.active .sub {
    color: var(--muted);
  }
</style>
