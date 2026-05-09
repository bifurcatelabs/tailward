<script>
  // Reads /llm-profiles to surface what tailward sends to the local LLM
  // and exposes the editable subset (endpoint / model / api key /
  // context window / temperature / max tokens) inline. Per-kind prompts
  // stay collapsed below.
  //
  // Save POSTs to /v2/config/local-llm. The daemon rewrites
  // ~/.tailward/config.toml in canonical form (drops unknown keys, no
  // comments preserved) and rebuilds the in-memory client so the new
  // endpoint / model take effect on the next call without a daemon
  // restart.

  let endpoint = $state(null);
  let profiles = $state([]);
  let loading = $state(true);

  // Editable form state. Initialized from /llm-profiles, mutated by
  // the user, posted on save. Never mutated by background refresh
  // while the user is editing — the dirty flag gates the refresh.
  let form = $state({
    endpoint: '',
    model: '',
    api_key: '',
    context_tokens: 32768,
    temperature: 0.6,
    max_tokens: 8000,
  });
  let dirty = $state(false);
  let saving = $state(false);
  let saveStatus = $state(null);

  function applyForm(ep) {
    form.endpoint = ep.endpoint || '';
    form.model = ep.default_model || '';
    form.api_key = '';
    form.context_tokens = ep.context_tokens ?? 32768;
    form.temperature = ep.temperature ?? 0.6;
    form.max_tokens = ep.max_tokens ?? 8000;
  }

  async function refresh() {
    try {
      const r = await fetch('/llm-profiles');
      if (!r.ok) return;
      const data = await r.json();
      endpoint = data.endpoint;
      profiles = data.profiles || [];
      if (!dirty && endpoint) applyForm(endpoint);
    } catch (e) {
      console.warn('llm-profiles fetch failed', e);
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    refresh();
    const t = setInterval(refresh, 30000);
    return () => clearInterval(t);
  });

  // Mark dirty on any user input so background refresh stops
  // overwriting their work-in-progress.
  function markDirty() { dirty = true; saveStatus = null; }

  async function save() {
    saving = true;
    saveStatus = null;
    const body = {
      local_llm_endpoint: form.endpoint.trim(),
      local_llm_model: form.model.trim(),
      local_llm_context_tokens: Number(form.context_tokens),
      local_llm_temperature: Number(form.temperature),
      local_llm_max_tokens: Number(form.max_tokens),
    };
    // api_key only sent when the user typed something; an empty field
    // means "leave unchanged" rather than "clear the key", so people
    // editing other fields don't accidentally wipe their auth.
    if (form.api_key.trim()) {
      body.local_llm_api_key = form.api_key.trim();
    }
    try {
      const r = await fetch('/v2/config/local-llm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const errText = await r.text();
        saveStatus = { ok: false, msg: `${r.status}: ${errText}` };
        return;
      }
      saveStatus = { ok: true, msg: 'saved — applied to next LLM call' };
      dirty = false;
      // Force-refresh so the displayed values match what got persisted
      // (and the api_key field clears).
      await refresh();
    } catch (e) {
      saveStatus = { ok: false, msg: String(e) };
    } finally {
      saving = false;
    }
  }

  let needsModel = $derived(endpoint && !endpoint.default_model);

  function fmt(v) {
    if (v == null) return '—';
    if (typeof v === 'number') return v.toString();
    return String(v);
  }
</script>

<section class="panel">
  <header>
    <div>
      <h3>local llm</h3>
      <p class="muted">
        endpoint, model, and shared sampler config for every LLM call
        tailward makes. saving rebuilds the client in-place — no daemon
        restart required.
      </p>
    </div>
  </header>

  {#if loading}
    <p class="empty">loading…</p>
  {:else}
    {#if needsModel}
      <div class="banner">
        <strong>no model configured.</strong> set a model name below
        and save before LLM-judged dimensions (rubric, drift, synth,
        consolidator) will fire.
      </div>
    {/if}

    <div class="form">
      <label class="row">
        <span class="lbl">endpoint URL</span>
        <input
          type="text"
          bind:value={form.endpoint}
          oninput={markDirty}
          placeholder="http://127.0.0.1:8080/v1"
          spellcheck="false"
        />
        <span class="hint">your local OpenAI-compatible server (llama.cpp, Ollama, vLLM, LM Studio, LiteLLM)</span>
      </label>

      <label class="row">
        <span class="lbl">model</span>
        <input
          type="text"
          bind:value={form.model}
          oninput={markDirty}
          placeholder="Qwen3.6-27B-Q6_K"
          spellcheck="false"
        />
        <span class="hint">name your server expects. recently tested known-good: Qwen3.6-27B (Q6_K for accuracy / Q4 for speed), Gemma-4-31B-Instruct</span>
      </label>

      <label class="row">
        <span class="lbl">api key</span>
        <input
          type="password"
          bind:value={form.api_key}
          oninput={markDirty}
          placeholder={endpoint?.api_key_set ? '(set, hidden — leave blank to keep)' : 'optional'}
          autocomplete="off"
        />
        <span class="hint">most local servers don't require this. leave blank to keep the existing value.</span>
      </label>

      <div class="row-grid">
        <label class="row">
          <span class="lbl">context window</span>
          <input
            type="number"
            min="2048"
            step="1024"
            bind:value={form.context_tokens}
            oninput={markDirty}
          />
          <span class="hint">tokens. used to size transcript slices.</span>
        </label>
        <label class="row">
          <span class="lbl">max tokens (output)</span>
          <input
            type="number"
            min="128"
            step="512"
            bind:value={form.max_tokens}
            oninput={markDirty}
          />
          <span class="hint">per call. thinking models need headroom.</span>
        </label>
        <label class="row">
          <span class="lbl">temperature</span>
          <input
            type="number"
            min="0"
            max="2"
            step="0.1"
            bind:value={form.temperature}
            oninput={markDirty}
          />
          <span class="hint">0.0 = deterministic, 2.0 = chaotic.</span>
        </label>
      </div>

      <div class="actions">
        <button
          class="save"
          onclick={save}
          disabled={saving || !dirty}
        >
          {saving ? 'saving…' : dirty ? 'save' : 'saved'}
        </button>
        {#if saveStatus}
          <span class="status" class:ok={saveStatus.ok} class:err={!saveStatus.ok}>
            {saveStatus.msg}
          </span>
        {/if}
      </div>
    </div>

    <div class="profiles">
      <div class="profiles-head">prompts per call kind</div>
      <p class="muted small">
        the system + user prompt templates tailward sends per call kind.
        sampler / model are the global values above; what differs per
        kind is the prompt.
      </p>
      {#each profiles as p}
        <details class="profile">
          <summary>
            <span class="kind">{p.kind}</span>
            <span class="caret" aria-hidden="true">▾</span>
          </summary>
          <div class="body">
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
  .small { font-size: 11px; }
  .empty { color: var(--muted); font-size: 12px; margin: 0; }

  .banner {
    background: rgba(232,153,104,0.10);
    border: 1px solid rgba(232,153,104,0.30);
    color: var(--accent);
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 12px;
    margin-bottom: 14px;
  }
  .banner strong { color: var(--text); }

  .form {
    display: flex;
    flex-direction: column;
    gap: 12px;
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
    margin-bottom: 18px;
  }
  .row {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .row .lbl {
    font-size: 10px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 600;
  }
  .row input {
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 4px;
    padding: 6px 9px;
    font-family: var(--mono);
    font-size: 12px;
    transition: border-color 120ms;
  }
  .row input:focus {
    outline: none;
    border-color: var(--accent);
  }
  .row .hint {
    font-size: 10px;
    color: var(--muted-deep);
  }
  .row-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 10px 16px;
  }
  @media (max-width: 720px) {
    .row-grid { grid-template-columns: 1fr; }
  }
  .actions {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 4px;
  }
  .save {
    background: var(--accent);
    color: var(--bg);
    border: 0;
    border-radius: 4px;
    padding: 6px 18px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    cursor: pointer;
    transition: opacity 120ms;
  }
  .save:disabled {
    opacity: 0.4;
    cursor: default;
  }
  .save:hover:not(:disabled) {
    opacity: 0.9;
  }
  .status {
    font-size: 11px;
    font-family: var(--mono);
  }
  .status.ok { color: var(--ok); }
  .status.err { color: var(--err); font-family: var(--mono); }

  .profiles {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .profiles-head {
    font-size: 10px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 600;
    margin-top: 6px;
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
    padding: 10px 16px;
    cursor: pointer;
    list-style: none;
  }
  summary::-webkit-details-marker { display: none; }
  .kind {
    font-family: var(--mono);
    font-size: 12px;
    color: var(--text);
    font-weight: 600;
    flex: 1;
  }
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
