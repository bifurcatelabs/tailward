import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import path from 'node:path';

// Build output goes into the FastAPI app's static mount so a plain
// `pip install` install can serve the bundled UI without any Node
// runtime. The dist/ directory is .gitignored — contributors need to
// run `npm install && npm run build` from this directory to populate
// it.
export default defineConfig({
  plugins: [svelte()],
  build: {
    outDir: path.resolve(__dirname, '../src/modmcp/web/static/dist'),
    emptyOutDir: true,
    manifest: true,
    rollupOptions: {
      input: path.resolve(__dirname, 'index.html'),
    },
  },
  // During development (`npm run dev`) the Vite dev server runs on
  // :5173 and proxies API + SSE traffic to the daemon on :7878 so SSE,
  // /replay, and /events all work without CORS friction.
  server: {
    port: 5173,
    proxy: {
      '/p': 'http://127.0.0.1:7878',
      '/api': 'http://127.0.0.1:7878',
      '/health': 'http://127.0.0.1:7878',
      '/hook': 'http://127.0.0.1:7878',
    },
  },
});
