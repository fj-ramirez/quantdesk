import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // `docker compose up` bind-mounts ./frontend into a Linux container from a Windows host
    // (see compose.override.yaml). inotify events do not cross that boundary, so Vite's default
    // filesystem watcher never fires and the dev server keeps serving the module graph it
    // built at startup -- editing a file changes nothing in the browser, and the failure is
    // silent: the file is still served on request, just from a stale transform. Diagnosed
    // 2026-09-09, when a whole afternoon of frontend work (T55's nav and T44's /scan page)
    // was invisible at localhost:5173 while every test passed, because the container had
    // been running since before any of it existed.
    //
    // Polling is the standard remedy and costs a little idle CPU; 300ms keeps saves feeling
    // immediate. `host: true` binds 0.0.0.0 so the published port actually reaches the
    // container rather than only its loopback.
    watch: { usePolling: true, interval: 300 },
    host: true,
    port: 5173,
  },
})
