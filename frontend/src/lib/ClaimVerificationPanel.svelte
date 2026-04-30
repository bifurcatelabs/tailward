<script>
  // Claim-verification verdicts across the project. Of the model's
  // first-person completion claims ("I removed X", "I added Y"),
  // how many held up under grep-based verification?
  //
  // Hybrid signal: claim text comes from Anthropic, the grep is local.
  // The audit *question* this answers is squarely third-party-facing
  // ("is the served model accurate?") so the panel lives on Platform.
  //
  // Reads /v2/reflection/{ph}'s `verification` field. URL is mis-named
  // for this surface — predates the source-of-data split.

  let { ph } = $props();

  let loading = $state(true);
  let error = $state(null);
  let data = $state(null);

  async function load() {
    if (!ph) return;
    loading = true;
    error = null;
    try {
      const r = await fetch(`/v2/reflection/${ph}?limit=1000`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      data = await r.json();
    } catch (e) {
      error = String(e);
    } finally {
      loading = false;
    }
  }

  $effect(() => { load(); });

  let rows = $derived.by(() => {
    const v = data?.verification;
    if (!v) return [];
    const total = (v.verified || 0) + (v.contradicted || 0) + (v.unverifiable || 0);
    return [
      { label: 'verified',     n: v.verified || 0,     total, kind: 'ok' },
      { label: 'contradicted', n: v.contradicted || 0, total, kind: 'err' },
      { label: 'unverifiable', n: v.unverifiable || 0, total, kind: 'muted' },
    ];
  });
</script>

<section class="panel">
  <header>
    <h3>claim verification verdicts</h3>
    <p class="muted">
      of the model's first-person completion claims, how many held up
      under grep-based verification.
    </p>
  </header>

  {#if loading && !data}
    <div class="empty">loading…</div>
  {:else if error}
    <div class="empty err">{error}</div>
  {:else}
    <div class="bars">
      {#each rows as r (r.label)}
        <div class="bar-row">
          <span class="bar-lbl">{r.label}</span>
          <span class="bar-track">
            <span class="bar-fill kind-{r.kind}"
              style="width: {r.total > 0 ? (r.n / r.total) * 100 : 0}%"></span>
          </span>
          <span class="bar-n">{r.n}</span>
        </div>
      {/each}
    </div>
    <div class="footnote">
      First-person completion claims, checked against the repo with
      grep. Test-fixture strings are excluded so removal claims
      aren't false-flagged by their own pinning tests.
    </div>
  {/if}
</section>

<style>
  .panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px;
  }
  header { margin-bottom: 14px; }
  header h3 {
    margin: 0;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.10em;
    color: var(--muted);
    font-weight: 600;
  }
  header p {
    margin: 4px 0 0;
    font-size: 12px;
    color: var(--muted-deep);
    line-height: 1.55;
  }

  .bars { display: flex; flex-direction: column; gap: 8px; }
  .bar-row {
    display: grid;
    grid-template-columns: 140px 1fr 36px;
    align-items: center;
    gap: 10px;
    font-size: 12px;
  }
  .bar-lbl { color: var(--text-soft); }
  .bar-track {
    background: var(--surface-2);
    height: 12px;
    border-radius: 3px;
    overflow: hidden;
  }
  .bar-fill {
    display: block;
    height: 100%;
    transition: width 240ms ease;
  }
  .bar-fill.kind-ok    { background: linear-gradient(90deg, rgba(95,195,167,0.4), var(--ok)); }
  .bar-fill.kind-err   { background: linear-gradient(90deg, rgba(232,122,122,0.4), var(--err)); }
  .bar-fill.kind-muted { background: var(--surface-3, rgba(255,255,255,0.08)); }
  .bar-n {
    color: var(--muted);
    font-family: var(--mono);
    font-size: 12px;
    text-align: right;
  }

  .footnote {
    margin-top: 12px;
    padding-top: 10px;
    border-top: 1px solid var(--border);
    color: var(--muted-deep);
    font-size: 11px;
    line-height: 1.55;
  }

  .empty {
    padding: 24px 12px;
    color: var(--muted);
    text-align: center;
    font-size: 13px;
  }
  .empty.err { color: var(--err); font-family: var(--mono); }
</style>
