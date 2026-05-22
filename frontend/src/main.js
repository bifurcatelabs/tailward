import { mount } from 'svelte';
import App from './App.svelte';
import { installDevLogMirror } from './lib/devlog.js';

// Install the console mirror before mounting so any early-init logs in
// the SPA already route through it. No-op outside dev-Tauri context.
installDevLogMirror();

// In a packaged Tauri build the main window is served from the bundled
// origin (``tauri.localhost``), not the daemon, so ``/``-rooted API + SSE
// paths would resolve against that origin and miss the daemon. Rewrite them
// to the daemon's absolute URL so the SPA reaches it cross-origin (the
// daemon allows the tauri origin via CORS). Over an SSH tunnel that same
// loopback URL is the remote daemon — so remote-attach works WITHOUT
// granting the remote any Tauri IPC; it only ever serves data. Skipped in
// the browser and in dev, which are already same-origin with the daemon.
(function patchDaemonOrigin() {
  if (typeof window === 'undefined') return;
  const isTauri = window.__TAURI_INTERNALS__ !== undefined;
  if (!isTauri || window.location.hostname === '127.0.0.1') return;
  const BASE = 'http://127.0.0.1:7878';
  const abs = (u) => (typeof u === 'string' && u.startsWith('/') ? BASE + u : u);
  const origFetch = window.fetch.bind(window);
  window.fetch = (input, init) => origFetch(abs(input), init);
  const OrigES = window.EventSource;
  if (OrigES) {
    window.EventSource = function (url, config) {
      return new OrigES(abs(url), config);
    };
    window.EventSource.prototype = OrigES.prototype;
  }
})();

// Parse the URL pathname into a page kind + props. The same SPA
// bundle serves three pages — ``/``, ``/p/<ph>``, and
// ``/p/<ph>/live/<sid>`` — distinguished by the path the daemon
// served the shell from. The data-* attributes on the mount node
// are kept as a fallback for the session page so existing routing
// (which sets data-ph / data-session-id) continues to work without
// requiring the route handlers to re-encode the URL.
const target = document.getElementById('app');

function parseRoute() {
  const path = (typeof window !== 'undefined' ? window.location.pathname : '') || '/';
  const session = path.match(/^\/p\/([^/]+)\/live\/([^/]+)\/?$/);
  if (session) {
    return { page: 'session', ph: session[1], sessionId: session[2] };
  }
  const project = path.match(/^\/p\/([^/]+)\/?$/);
  if (project) {
    return { page: 'project', ph: project[1], sessionId: '' };
  }
  return { page: 'landing', ph: '', sessionId: '' };
}

const route = parseRoute();

const app = mount(App, {
  target,
  props: {
    page: route.page,
    ph: target.dataset.ph || route.ph,
    sessionId: target.dataset.sessionId || route.sessionId,
  },
});

export default app;
