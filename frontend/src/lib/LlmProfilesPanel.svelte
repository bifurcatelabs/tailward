<script>
  // Reads /llm-profiles → renders two profile cards (identity + full
  // sampler config) side by side, a routing matrix below, and the
  // per-call-kind prompt transparency at the bottom.
  //
  // Save POSTs to /v2/config/local-llm. The daemon rewrites
  // ~/.tailward/config.toml in canonical form and rebuilds the
  // in-memory client so endpoint / model / sampler changes take
  // effect on the next call without a daemon restart.

  let profiles = $state([]);
  let routing = $state({});
  let callProfiles = $state([]);
  let loading = $state(true);

  // Editable form state. Initialized from /llm-profiles, mutated by
  // the user, posted on save. ``dirty`` gates background refresh so
  // a periodic poll doesn't overwrite work-in-progress.
  const CALL_KINDS = [
    'drift', 'audit', 'rubric', 'user_rubric',
    'synth', 'consolidator', 'query',
  ];

  function blankProfile() {
    return {
      enabled: true,
      endpoint: '',
      api_key: '',
      model: '',
      context_tokens: 32768,
      temperature: 0.6,
      top_p: 0.95,
      top_k: 20,
      min_p: 0.0,
      presence_penalty: 0.0,
      repetition_penalty: 1.0,
      max_tokens: 8000,
    };
  }

  let form = $state({
    p1: blankProfile(),
    p2: { ...blankProfile(), enabled: false },
    routing: Object.fromEntries(CALL_KINDS.map((k) => [k, 1])),
  });
  let dirty = $state(false);
  let saving = $state(false);
  let saveStatus = $state(null);

  function applyServerState() {
    const p1 = profiles[0] || {};
    const p2 = profiles[1] || {};
    form.p1 = {
      enabled: true,
      endpoint: p1.endpoint || '',
      api_key: '',
      model: p1.model || '',
      context_tokens: p1.context_tokens ?? 32768,
      temperature: p1.temperature ?? 0.6,
      top_p: p1.top_p ?? 0.95,
      top_k: p1.top_k ?? 20,
      min_p: p1.min_p ?? 0.0,
      presence_penalty: p1.presence_penalty ?? 0.0,
      repetition_penalty: p1.repetition_penalty ?? 1.0,
      max_tokens: p1.max_tokens ?? 8000,
    };
    form.p2 = {
      enabled: !!p2.enabled,
      endpoint: p2.endpoint || '',
      api_key: '',
      model: p2.model || '',
      context_tokens: p2.context_tokens ?? 32768,
      temperature: p2.temperature ?? 0.6,
      top_p: p2.top_p ?? 0.95,
      top_k: p2.top_k ?? 20,
      min_p: p2.min_p ?? 0.0,
      presence_penalty: p2.presence_penalty ?? 0.0,
      repetition_penalty: p2.repetition_penalty ?? 1.0,
      max_tokens: p2.max_tokens ?? 8000,
    };
    for (const kind of CALL_KINDS) {
      form.routing[kind] = routing[kind] ?? 1;
    }
  }

  async function refresh() {
    try {
      const r = await fetch('/llm-profiles');
      if (!r.ok) return;
      const data = await r.json();
      profiles = data.profiles || [];
      routing = data.routing || {};
      callProfiles = data.call_profiles || [];
      if (!dirty) applyServerState();
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

  function markDirty() { dirty = true; saveStatus = null; }

  // Server stores api_key as a real string; an empty field on the form
  // means "leave unchanged" rather than "clear it." Only include the
  // api_key in the POST body when the user typed something.
  function profileBody(prefix, p, includeEnabled) {
    const body = {
      [`${prefix}_endpoint`]: p.endpoint.trim(),
      [`${prefix}_model`]: p.model.trim(),
      [`${prefix}_context_tokens`]: Number(p.context_tokens),
      [`${prefix}_temperature`]: Number(p.temperature),
      [`${prefix}_top_p`]: Number(p.top_p),
      [`${prefix}_top_k`]: Number(p.top_k),
      [`${prefix}_min_p`]: Number(p.min_p),
      [`${prefix}_presence_penalty`]: Number(p.presence_penalty),
      [`${prefix}_repetition_penalty`]: Number(p.repetition_penalty),
      [`${prefix}_max_tokens`]: Number(p.max_tokens),
    };
    if (p.api_key.trim()) body[`${prefix}_api_key`] = p.api_key.trim();
    if (includeEnabled) body.local_llm_2_enabled = !!p.enabled;
    return body;
  }

  async function save() {
    saving = true;
    saveStatus = null;
    const body = {
      ...profileBody('local_llm_1', form.p1, false),
      ...profileBody('local_llm_2', form.p2, true),
    };
    for (const kind of CALL_KINDS) {
      body[`local_llm_route_${kind}`] = Number(form.routing[kind]);
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
      await refresh();
    } catch (e) {
      saveStatus = { ok: false, msg: String(e) };
    } finally {
      saving = false;
    }
  }

  let needsModel = $derived(
    profiles.length > 0 && !profiles[0]?.model
  );
</script>

<section class="panel">
  <header>
    <div>
      <h3>local llm</h3>
      <p class="muted">
        two profiles, each fully self-contained (endpoint + auth +
        model + sampler). profile 1 is the default; profile 2 is
        opt-in. workers route to either via the matrix below. saving
        rebuilds the in-memory client — no daemon restart required.
      </p>
    </div>
  </header>

  {#if loading}
    <p class="empty">loading…</p>
  {:else}
    {#if needsModel}
      <div class="banner">
        <strong>no model configured on profile 1.</strong> set a model
        name below and save before LLM-judged dimensions (rubric, drift,
        synth, consolidator) will fire.
      </div>
    {/if}

    <div class="profiles-grid">
      {#each [{ key: 'p1', label: 'profile 1', toggleable: false }, { key: 'p2', label: 'profile 2', toggleable: true }] as slot}
        {@const p = form[slot.key]}
        <div class="profile-card" class:disabled={slot.toggleable && !p.enabled}>
          <div class="card-head">
            <span class="card-label">{slot.label}</span>
            {#if slot.toggleable}
              <label class="toggle">
                <input
                  type="checkbox"
                  bind:checked={p.enabled}
                  onchange={markDirty}
                />
                <span>enabled</span>
              </label>
            {:else}
              <span class="default-pill">default</span>
            {/if}
          </div>

          <div class="section-head">identity</div>
          <label class="row">
            <span class="lbl">endpoint URL</span>
            <input
              type="text"
              bind:value={p.endpoint}
              oninput={markDirty}
              placeholder="http://127.0.0.1:8080/v1"
              spellcheck="false"
              disabled={slot.toggleable && !p.enabled}
            />
          </label>
          <label class="row">
            <span class="lbl">model</span>
            <input
              type="text"
              bind:value={p.model}
              oninput={markDirty}
              placeholder="Qwen3.6-27B-Q6_K"
              spellcheck="false"
              disabled={slot.toggleable && !p.enabled}
            />
          </label>
          <label class="row">
            <span class="lbl">api key</span>
            <input
              type="password"
              bind:value={p.api_key}
              oninput={markDirty}
              placeholder={(profiles[slot.key === 'p1' ? 0 : 1]?.api_key_set) ? '(set, hidden — leave blank to keep)' : 'optional'}
              autocomplete="off"
              disabled={slot.toggleable && !p.enabled}
            />
          </label>
          <label class="row">
            <span class="lbl">context window (tokens)</span>
            <input
              type="number" min="2048" step="1024"
              bind:value={p.context_tokens}
              oninput={markDirty}
              disabled={slot.toggleable && !p.enabled}
            />
          </label>

          <div class="section-head">sampler</div>
          <div class="sampler-grid">
            <label class="row">
              <span class="lbl">temperature</span>
              <input
                type="number" min="0" max="2" step="0.05"
                bind:value={p.temperature}
                oninput={markDirty}
                disabled={slot.toggleable && !p.enabled}
              />
            </label>
            <label class="row">
              <span class="lbl">top_p</span>
              <input
                type="number" min="0" max="1" step="0.01"
                bind:value={p.top_p}
                oninput={markDirty}
                disabled={slot.toggleable && !p.enabled}
              />
            </label>
            <label class="row">
              <span class="lbl">top_k</span>
              <input
                type="number" min="0" step="1"
                bind:value={p.top_k}
                oninput={markDirty}
                disabled={slot.toggleable && !p.enabled}
              />
            </label>
            <label class="row">
              <span class="lbl">min_p</span>
              <input
                type="number" min="0" max="1" step="0.01"
                bind:value={p.min_p}
                oninput={markDirty}
                disabled={slot.toggleable && !p.enabled}
              />
            </label>
            <label class="row">
              <span class="lbl">presence_penalty</span>
              <input
                type="number" min="-2" max="2" step="0.05"
                bind:value={p.presence_penalty}
                oninput={markDirty}
                disabled={slot.toggleable && !p.enabled}
              />
            </label>
            <label class="row">
              <span class="lbl">repetition_penalty</span>
              <input
                type="number" min="0" step="0.05"
                bind:value={p.repetition_penalty}
                oninput={markDirty}
                disabled={slot.toggleable && !p.enabled}
              />
            </label>
            <label class="row">
              <span class="lbl">max_tokens (output)</span>
              <input
                type="number" min="128" step="512"
                bind:value={p.max_tokens}
                oninput={markDirty}
                disabled={slot.toggleable && !p.enabled}
              />
            </label>
          </div>
        </div>
      {/each}
    </div>

    <div class="routing-card">
      <div class="card-head">
        <span class="card-label">routing matrix</span>
        <span class="hint">which profile handles each worker's calls</span>
      </div>
      <div class="routing-grid">
        {#each CALL_KINDS as kind}
          <div class="routing-row">
            <span class="kind-label">{kind}</span>
            <div class="route-toggle">
              <label>
                <input
                  type="radio"
                  name="route-{kind}"
                  value={1}
                  bind:group={form.routing[kind]}
                  onchange={markDirty}
                />
                <span>1</span>
              </label>
              <label class:dimmed={!form.p2.enabled}>
                <input
                  type="radio"
                  name="route-{kind}"
                  value={2}
                  bind:group={form.routing[kind]}
                  onchange={markDirty}
                  disabled={!form.p2.enabled}
                />
                <span>2</span>
              </label>
            </div>
          </div>
        {/each}
      </div>
    </div>

    <div class="actions">
      <button class="save" onclick={save} disabled={saving || !dirty}>
        {saving ? 'saving…' : dirty ? 'save changes' : 'saved'}
      </button>
      {#if saveStatus}
        <span class="status" class:ok={saveStatus.ok} class:err={!saveStatus.ok}>
          {saveStatus.msg}
        </span>
      {/if}
    </div>

    <div class="prompts">
      <div class="prompts-head">prompts per call kind</div>
      <p class="muted small">
        system + user prompt templates tailward sends, with the routed
        profile + its sampler values at the moment of each call.
      </p>
      {#each callProfiles as p}
        <details class="prompt-card">
          <summary>
            <span class="kind">{p.kind}</span>
            <span class="route-badge">profile {p.profile ?? 1}</span>
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

  .profiles-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 14px;
    margin-bottom: 14px;
  }
  @media (max-width: 900px) {
    .profiles-grid { grid-template-columns: 1fr; }
  }
  .profile-card {
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .profile-card.disabled { opacity: 0.55; }
  .card-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 4px;
  }
  .card-label {
    font-size: 11px;
    color: var(--text);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 700;
  }
  .default-pill {
    font-size: 9px;
    color: var(--accent);
    border: 1px solid rgba(232,153,104,0.40);
    background: rgba(232,153,104,0.08);
    border-radius: 999px;
    padding: 2px 8px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 600;
  }
  .toggle {
    display: flex;
    align-items: center;
    gap: 6px;
    cursor: pointer;
    font-size: 10px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 600;
  }
  .section-head {
    font-size: 9px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.10em;
    font-weight: 600;
    margin-top: 6px;
    margin-bottom: -2px;
  }
  .row {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .row .lbl {
    font-size: 10px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-weight: 600;
  }
  .row input {
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 4px;
    padding: 5px 8px;
    font-family: var(--mono);
    font-size: 11px;
    transition: border-color 120ms;
  }
  .row input:focus { outline: none; border-color: var(--accent); }
  .row input:disabled { opacity: 0.5; cursor: default; }
  .sampler-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px 12px;
  }
  @media (max-width: 480px) {
    .sampler-grid { grid-template-columns: 1fr; }
  }

  .routing-card {
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
    margin-bottom: 14px;
  }
  .hint {
    font-size: 10px;
    color: var(--muted-deep);
    text-transform: none;
    letter-spacing: 0;
    font-weight: 500;
  }
  .routing-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 8px 14px;
    margin-top: 8px;
  }
  .routing-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 4px 0;
  }
  .kind-label {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--text-soft);
  }
  .route-toggle { display: flex; gap: 4px; }
  .route-toggle label {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    font-size: 10px;
    color: var(--muted);
    cursor: pointer;
    padding: 2px 6px;
    border: 1px solid var(--border);
    border-radius: 4px;
  }
  .route-toggle label.dimmed { opacity: 0.5; cursor: default; }
  .route-toggle input { margin: 0; }

  .actions {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 18px;
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
  .save:disabled { opacity: 0.4; cursor: default; }
  .save:hover:not(:disabled) { opacity: 0.9; }
  .status { font-size: 11px; font-family: var(--mono); }
  .status.ok { color: var(--ok); }
  .status.err { color: var(--err); }

  .prompts {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .prompts-head {
    font-size: 10px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 600;
    margin-top: 6px;
  }
  .prompt-card {
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
  }
  summary {
    display: flex;
    align-items: center;
    gap: 10px;
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
  .route-badge {
    font-size: 9px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    border: 1px solid var(--border);
    border-radius: 999px;
    padding: 1px 7px;
  }
  .caret { color: var(--muted-deep); transition: transform 200ms ease; }
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
