<script>
  // Chassis-only: this is the v0.2 entrypoint proving the build
  // pipeline works end-to-end. The vanilla JS live page (live.js +
  // live.html) stays the production surface; this v2 surface exists
  // so subsequent commits can port the feed, header strip, charts,
  // and new dashboards into Svelte components without taking the
  // working v1.1 UI offline.
  let { ph = '', sessionId = '' } = $props();

  let health = $state('checking…');
  let healthDetail = $state(null);

  $effect(() => {
    fetch('/health')
      .then((r) => r.json())
      .then((data) => {
        health = data.ok ? 'ok' : 'unhealthy';
        healthDetail = data;
      })
      .catch((err) => {
        health = 'error';
        healthDetail = { error: String(err) };
      });
  });
</script>

<header>
  <h1>warden</h1>
  <p class="muted">v0.2 chassis · Svelte build pipeline</p>
</header>

<section class="card">
  <h2>Mount context</h2>
  <dl>
    <dt>project hash</dt>
    <dd>{ph || '(none)'}</dd>
    <dt>session id</dt>
    <dd>{sessionId || '(none)'}</dd>
    <dt>daemon /health</dt>
    <dd class="health-{health}">{health}</dd>
  </dl>
  {#if healthDetail}
    <pre>{JSON.stringify(healthDetail, null, 2)}</pre>
  {/if}
</section>

<section class="card">
  <h2>Where this goes</h2>
  <p>
    Subsequent v0.2 commits port the live feed, header strip, scope
    snapshots, and rubric bars into Svelte components, then add the
    Reflection and Platform views.
  </p>
  <p>
    The legacy live page is still at
    <a href={`/p/${ph}/live/${sessionId}`}>/p/{ph}/live/{sessionId}</a>
    and remains the production audit surface until this one fully
    covers it.
  </p>
</section>

<style>
  :global(body) {
    margin: 0;
    background: #0e0f13;
    color: #d6dbe5;
    font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI",
      Roboto, sans-serif;
    line-height: 1.5;
  }
  header {
    padding: 24px 32px 8px;
    border-bottom: 1px solid #1f232c;
  }
  header h1 {
    margin: 0;
    font-size: 18px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  .muted {
    color: #6b7382;
    font-size: 12px;
    margin: 4px 0 0;
  }
  .card {
    background: #161922;
    border: 1px solid #2a2e38;
    border-radius: 8px;
    margin: 16px 32px;
    padding: 16px 20px;
  }
  .card h2 {
    margin: 0 0 12px;
    font-size: 13px;
    color: #8a93a4;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  dl {
    display: grid;
    grid-template-columns: 140px 1fr;
    gap: 6px 16px;
    margin: 0;
  }
  dt {
    color: #8a93a4;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  dd {
    margin: 0;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 13px;
  }
  .health-ok {
    color: #6fcf97;
  }
  .health-error,
  .health-unhealthy {
    color: #eb5757;
  }
  pre {
    margin-top: 12px;
    background: #0c0e13;
    border-left: 2px solid #2a2e38;
    padding: 8px 12px;
    font-size: 12px;
    overflow: auto;
  }
  a {
    color: #6fa9ff;
  }
</style>
