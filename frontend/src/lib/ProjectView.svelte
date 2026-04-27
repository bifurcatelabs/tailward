<script>
  /**
   * Project rules view — replaces the legacy Jinja intent editor.
   *
   * Read-only. Shows the parsed CompiledPolicy (allow/deny globs,
   * immutable paths, forbidden bash patterns) + the active
   * session_mode for the project. Points users at the on-disk
   * ``intent.md`` for editing — the file is the source of truth and
   * users edit it in their editor of choice.
   *
   * The deliberate non-feature here: there is no in-browser editor.
   * v1's session-handoff workflow (warden handoff → intent editor)
   * is being deprecated; intent.md persists as a per-project rules
   * config artifact, not as a UI-driven document. See memory:
   * project_v3_handoff_deprecation.md.
   */
  let { ph } = $props();

  let loading = $state(true);
  let error = $state(null);
  let data = $state(null);

  async function load() {
    loading = true;
    error = null;
    try {
      const r = await fetch(`/v2/projects/${ph}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      data = await r.json();
    } catch (e) {
      error = String(e);
    } finally {
      loading = false;
    }
  }

  $effect(() => { load(); });

  function shortName(projectPath) {
    if (!projectPath) return '—';
    const parts = String(projectPath).replace(/\\/g, '/').split('/').filter(Boolean);
    return parts[parts.length - 1] || projectPath;
  }
</script>

<section class="view">
  {#if loading && !data}
    <div class="empty">loading…</div>
  {:else if error}
    <div class="empty err">{error}</div>
  {:else if !data}
    <div class="empty">no data</div>
  {:else}
    <header class="hd">
      <div class="title-row">
        <h2>{shortName(data.project_path)}</h2>
        {#if data.session_mode}
          <span class="mode">{data.session_mode}</span>
        {:else}
          <span class="mode mode-default" title={data.mode_profile?.description || ''}>default</span>
        {/if}
        <button class="reload" onclick={load} disabled={loading} title="refresh">↻</button>
      </div>
      <div class="path-row">
        <span class="muted">path</span>
        <code class="path">{data.project_path}</code>
      </div>
      {#if data.latest_session_id}
        <div class="path-row">
          <span class="muted">latest session</span>
          <a class="session-link" href={`/p/${ph}/live/${data.latest_session_id}`}>
            {data.latest_session_id.slice(0, 8)} →
          </a>
        </div>
      {/if}
    </header>

    <div class="grid">
      <!-- ALLOW / DENY -->
      <div class="card">
        <h3>path policy</h3>
        {#if data.rules.path.allow.length || data.rules.path.deny.length}
          {#if data.rules.path.allow.length}
            <div class="sub">allow</div>
            <ul class="rules-list">
              {#each data.rules.path.allow as p (p)}
                <li><code>{p}</code></li>
              {/each}
            </ul>
          {/if}
          {#if data.rules.path.deny.length}
            <div class="sub">deny</div>
            <ul class="rules-list">
              {#each data.rules.path.deny as p (p)}
                <li><code>{p}</code></li>
              {/each}
            </ul>
          {/if}
        {:else}
          <div class="empty inline">no path rules</div>
        {/if}
      </div>

      <!-- IMMUTABLE -->
      <div class="card">
        <h3>immutable files</h3>
        {#if data.rules.immutable.length}
          <ul class="rules-list">
            {#each data.rules.immutable as p (p)}
              <li><code>{p}</code></li>
            {/each}
          </ul>
        {:else}
          <div class="empty inline">no immutable paths</div>
        {/if}
      </div>

      <!-- FORBIDDEN BASH -->
      <div class="card wide">
        <h3>forbidden bash patterns</h3>
        {#if data.rules.bash.length}
          <ul class="rules-list">
            {#each data.rules.bash as p (p)}
              <li><code>{p}</code></li>
            {/each}
          </ul>
        {:else}
          <div class="empty inline">no forbidden-bash patterns beyond the baseline</div>
        {/if}
      </div>

      <!-- RULE TEXTS -->
      {#if data.rules.rule_texts && Object.keys(data.rules.rule_texts).length}
        <div class="card wide">
          <h3>rule sources</h3>
          <p class="muted small">verbatim bullets from the project's <code>intent.md</code> Active Rules section, each parsed into the policies above.</p>
          <ul class="rules-list">
            {#each Object.entries(data.rules.rule_texts) as [rid, text] (rid)}
              <li>
                <span class="rid">{rid}</span>
                <span>{text}</span>
              </li>
            {/each}
          </ul>
        </div>
      {/if}
    </div>

    <p class="footnote">
      Read-only. <code>intent.md</code> is the source of truth and lives at
      <code>{data.intent_path}</code>. Edit it in your editor of choice; the
      daemon re-reads on each turn so changes land without restart. The
      v1 in-browser intent editor was retired in v2.1.
    </p>
  {/if}
</section>

<style>
  .view {
    padding: 32px 24px;
    max-width: 1100px;
    margin: 0 auto;
  }
  .empty {
    padding: 48px 12px;
    color: var(--muted);
    text-align: center;
    font-size: 13px;
  }
  .empty.err { color: var(--err); font-family: var(--mono); }
  .empty.inline {
    padding: 8px 0;
    text-align: left;
    font-size: 12px;
  }

  .hd { margin-bottom: 28px; }
  .title-row {
    display: flex;
    align-items: baseline;
    gap: 12px;
  }
  .title-row h2 {
    margin: 0;
    font-size: 22px;
    font-weight: 600;
    letter-spacing: -0.01em;
  }
  .mode {
    font-family: var(--mono);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 2px 6px;
    border-radius: 999px;
    background: rgba(232,153,104,0.10);
    color: var(--accent);
    border: 1px solid rgba(232,153,104,0.30);
  }
  .mode.mode-default {
    background: var(--surface-2);
    color: var(--muted);
    border-color: var(--border);
  }
  .reload {
    background: transparent;
    border: 0;
    color: var(--muted);
    cursor: pointer;
    font-size: 16px;
    padding: 0 6px;
    margin-left: auto;
  }
  .reload:hover:not(:disabled) { color: var(--text); }
  .reload:disabled { opacity: 0.4; cursor: default; }

  .path-row {
    display: flex;
    gap: 8px;
    align-items: baseline;
    margin-top: 8px;
    font-size: 12px;
  }
  .path-row .muted {
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-size: 10px;
  }
  .path {
    font-family: var(--mono);
    color: var(--text-soft);
  }
  .session-link {
    font-family: var(--mono);
    color: var(--accent);
    text-decoration: none;
  }
  .session-link:hover { text-decoration: underline; }

  .grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px;
  }
  @media (max-width: 800px) { .grid { grid-template-columns: 1fr; } }
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px;
  }
  .card.wide { grid-column: 1 / -1; }
  .card h3 {
    margin: 0 0 10px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.10em;
    color: var(--muted);
    font-weight: 600;
  }
  .sub {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--muted-deep);
    margin: 6px 0 4px;
  }

  .rules-list {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .rules-list li {
    display: flex;
    gap: 8px;
    align-items: baseline;
    font-size: 12px;
    color: var(--text-soft);
    line-height: 1.5;
  }
  .rules-list code {
    font-family: var(--mono);
    background: var(--surface-2);
    padding: 1px 6px;
    border-radius: 3px;
    color: var(--text);
    font-size: 11px;
  }
  .rid {
    font-family: var(--mono);
    color: var(--muted-deep);
    font-size: 10px;
    min-width: 50px;
  }

  .footnote {
    margin-top: 28px;
    color: var(--muted-deep);
    font-size: 11px;
    line-height: 1.6;
    border-left: 2px solid var(--border-strong);
    padding-left: 12px;
  }
  .footnote code {
    font-family: var(--mono);
    background: var(--surface-2);
    padding: 1px 5px;
    border-radius: 3px;
    color: var(--muted);
  }
  .muted { color: var(--muted); }
  .small { font-size: 11px; }
</style>
