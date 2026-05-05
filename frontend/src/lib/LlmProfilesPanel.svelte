<script>
  // Reads /llm-profiles and surfaces, per call kind, the model
  // tailward uses, the sampler params it's configured with, and the
  // verbatim prompts it sends to the local LLM. Collapsible cards
  // so the panel stays compact by default and the user opens what
  // they want to inspect.

  let endpoint = $state(null);
  let profiles = $state([]);
  let loading = $state(true);

  async function refresh() {
    try {
      const r = await fetch('/llm-profiles');
      if (!r.ok) return;
      const data = await r.json();
      endpoint = data.endpoint;
      profiles = data.profiles || [];
    } catch (e) {
      console.warn('llm-profiles fetch failed', e);
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    refresh();
    // Profiles change rarely (config edits + daemon restart). Re-poll
    // every 30s anyway so a user editing config and reloading sees
    // updates without a hard reload.
    const t = setInterval(refresh, 30000);
    return () => clearInterval(t);
  });

  function fmt(v) {
    if (v == null) return '—';
    if (typeof v === 'number') return v.toString();
    if (typeof v === 'boolean') return v ? 'on' : 'off';
    return String(v);
  }
</script>

<section class="panel">
  <header>
    <div>
      <h3>local llm profiles</h3>
      <p class="muted">
        the model, sampler params, and prompts sent to the local endpoint
        per call kind. read directly from the live config — what's shown
        is what's running.
      </p>
    </div>
  </header>

  {#if loading}
    <p class="empty">loading…</p>
  {:else}
    {#if endpoint}
      <div class="endpoint">
        <div class="ep-row">
          <span class="ep-label">endpoint</span>
          <code>{endpoint.endpoint}</code>
        </div>
        <div class="ep-row">
          <span class="ep-label">default model</span>
          <code>{endpoint.default_model}</code>
        </div>
        <div class="ep-row">
          <span class="ep-label">context window</span>
          <code>{endpoint.context_tokens.toLocaleString()} tokens</code>
        </div>
        <div class="ep-row">
          <span class="ep-label">api key</span>
          <span class="muted">{endpoint.api_key_set ? 'set (value hidden)' : 'not set'}</span>
        </div>
      </div>
    {/if}

    <div class="profiles">
      {#each profiles as p}
        <details class="profile">
          <summary>
            <span class="kind">{p.kind}</span>
            <span class="badges">
              <span class="badge" title="configured model">
                {p.model}{p.model_overridden ? ' (override)' : ''}
              </span>
              <span class="badge mono">temp {fmt(p.temperature)}</span>
              <span class="badge mono">presence {fmt(p.presence_penalty)}</span>
              <span class="badge mono">max {fmt(p.max_tokens)}</span>
              <span class="badge {p.enable_thinking ? 'badge-on' : 'badge-off'}">
                thinking {fmt(p.enable_thinking)}
              </span>
            </span>
            <span class="caret" aria-hidden="true">▾</span>
          </summary>
          <div class="body">
            <div class="grid">
              <span class="g-label">model</span>
              <code>{p.model}</code>
              <span class="g-label">max_tokens</span>
              <code>{p.max_tokens}</code>
              <span class="g-label">temperature</span>
              <code>{fmt(p.temperature)}</code>
              <span class="g-label">presence_penalty</span>
              <code>{fmt(p.presence_penalty)}</code>
              <span class="g-label">top_p / top_k / min_p</span>
              <code>{p.top_p} / {p.top_k} / {p.min_p}</code>
              <span class="g-label">repetition_penalty</span>
              <code>{p.repetition_penalty}</code>
              <span class="g-label">enable_thinking</span>
              <code>{fmt(p.enable_thinking)}</code>
            </div>

            <div class="prompt">
              <div class="prompt-head">system prompt</div>
              <pre>{p.system_prompt}</pre>
            </div>
            <div class="prompt">
              <div class="prompt-head">user prompt template</div>
              <pre>{p.user_prompt_template}</pre>
            </div>
          </div>
        </details>
      {/each}
    </div>
  {/if}
</section>

<style>
  .panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 20px 22px;
  }
  header { margin-bottom: 14px; }
  h3 {
    margin: 0;
    font-size: 14px;
    font-weight: 600;
    color: var(--text);
  }
  .muted {
    color: var(--muted);
    margin: 4px 0 0;
    font-size: 11px;
    line-height: 1.55;
    max-width: 720px;
  }
  .empty { color: var(--muted); font-size: 12px; margin: 0; }

  .endpoint {
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 16px;
    margin-bottom: 14px;
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px 24px;
  }
  @media (max-width: 760px) { .endpoint { grid-template-columns: 1fr; } }
  .ep-row {
    display: flex;
    align-items: baseline;
    gap: 10px;
    font-size: 12px;
  }
  .ep-label {
    color: var(--muted);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 600;
    min-width: 110px;
  }
  .ep-row code {
    font-family: var(--mono);
    color: var(--text);
    font-size: 11px;
    background: var(--bg);
    padding: 1px 7px;
    border-radius: 4px;
    border: 1px solid var(--border);
  }

  .profiles {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .profile {
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
  }
  summary {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 16px;
    cursor: pointer;
    list-style: none;
    /* user-select intentionally NOT none — users want to copy the
       kind name + sampler params from the summary header. The
       toggle works on click; double-click selects without toggling. */
  }
  summary::-webkit-details-marker { display: none; }
  .kind {
    font-family: var(--mono);
    font-size: 12px;
    color: var(--text);
    font-weight: 600;
    min-width: 100px;
  }
  .badges {
    flex: 1;
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .badge {
    font-size: 10px;
    padding: 2px 8px;
    border-radius: 999px;
    border: 1px solid var(--border);
    color: var(--text-soft);
    background: var(--bg);
    white-space: nowrap;
  }
  .badge.mono { font-family: var(--mono); }
  .badge-on  { color: var(--ok); border-color: rgba(95,195,167,0.30); background: rgba(95,195,167,0.06); }
  .badge-off { color: var(--muted); }
  .caret {
    color: var(--muted-deep);
    transition: transform 200ms ease;
  }
  details[open] .caret { transform: rotate(180deg); }

  .body {
    border-top: 1px solid var(--border);
    padding: 14px 16px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px 18px;
    align-items: baseline;
    font-size: 12px;
  }
  @media (max-width: 540px) { .grid { grid-template-columns: 1fr; } }
  .g-label {
    color: var(--muted);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 600;
  }
  .body code {
    font-family: var(--mono);
    color: var(--text);
    font-size: 11px;
    background: var(--bg);
    padding: 1px 7px;
    border-radius: 4px;
    border: 1px solid var(--border);
    justify-self: start;
  }

  .prompt-head {
    font-size: 10px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 600;
    margin-bottom: 4px;
  }
  pre {
    margin: 0;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 10px 12px;
    font-family: var(--mono);
    font-size: 11.5px;
    line-height: 1.55;
    color: var(--text-soft);
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 320px;
    overflow: auto;
  }
</style>
