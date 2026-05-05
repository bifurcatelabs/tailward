<script>
  /**
   * Lightweight top strip for non-session pages (landing, project).
   * The session page uses ``HeaderBar`` (richer chrome with session
   * codes, mode pill, connection indicator, picker). Both share the
   * same brand mark.
   */
</script>

<header class="strip">
  <a href="/" class="brand">
    <svg class="mark" viewBox="0 0 32 32" aria-hidden="true">
      <defs>
        <linearGradient id="markFill" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stop-color="var(--accent)" stop-opacity="0.55"/>
          <stop offset="100%" stop-color="var(--accent-soft)" stop-opacity="1"/>
        </linearGradient>
      </defs>
      <path
        d="M3 9 L8 23 L13 13 L18 23 L23 9 M14 22 L19 12 L24 22"
        fill="none"
        stroke="url(#markFill)"
        stroke-width="2.2"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
    </svg>
    <span class="name">tailward</span>
    <span class="version">2.8.0</span>
  </a>
  <span class="tagline">audit underlay for Claude Code</span>
</header>

<style>
  .strip {
    display: flex;
    align-items: baseline;
    gap: 18px;
    padding: 16px 24px;
    border-bottom: 1px solid var(--border);
    backdrop-filter: blur(8px);
    position: relative;
    z-index: 100;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 10px;
    text-decoration: none;
    color: inherit;
  }
  .mark {
    width: 22px;
    height: 22px;
    filter: drop-shadow(0 0 6px var(--accent-glow));
  }
  .name {
    font-weight: 600;
    letter-spacing: 0.01em;
    font-size: 15px;
    color: var(--text);
  }
  .version {
    color: var(--muted);
    font-size: 10px;
    font-family: var(--mono);
    padding: 2px 6px;
    border: 1px solid var(--border);
    border-radius: 999px;
    letter-spacing: 0.04em;
  }
  .tagline {
    color: var(--muted);
    font-size: 12px;
    letter-spacing: 0.02em;
  }
</style>
