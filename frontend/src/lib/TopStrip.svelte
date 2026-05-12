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
    <svg class="mark" viewBox="0 0 510 323" aria-hidden="true">
      <!-- TW monogram — kept in sync with HeaderBar.svelte and the
           standalone src-tauri/icons/tailward.svg source for tray /
           taskbar / favicon generation. Update all three together. -->
      <defs>
        <linearGradient id="markFillTop" x1="0" y1="323" x2="510" y2="0" gradientUnits="userSpaceOnUse">
          <stop offset="0" stop-color="var(--accent)" stop-opacity="0.55"/>
          <stop offset="1" stop-color="var(--accent-soft)" stop-opacity="1"/>
        </linearGradient>
      </defs>
      <g
        fill="none"
        stroke="url(#markFillTop)"
        stroke-width="44"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <path d="M34 51H279"/>
        <path d="M128 51V252 C128 270 141 281 159 281 H187"/>
        <path d="M200 112 C212 112 218 122 225 136 L286 260 L342 151 L402 268 L494 60"/>
      </g>
    </svg>
    <span class="name">tailward</span>
    <span class="version">2.10.0</span>
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
    /* Same icon→lowercase-text alignment nudge as HeaderBar — pushes
       the visual midline down to match the "tailward" glyph center. */
    margin-top: 2px;
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
