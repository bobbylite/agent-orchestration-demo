import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      // Same-origin in dev so the encrypted session cookies round-trip
      // without needing cross-site cookie configuration.
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      // task-agent's own /telemetry endpoint (app/telemetry.py) — proxied
      // rather than given CORS middleware, same "same-origin in dev"
      // reasoning as /api above. No cookies involved here (task-agent's
      // /telemetry isn't session-gated), but this keeps every cross-service
      // call the diagram makes going through one pattern.
      '/task-agent-api': {
        target: 'http://localhost:9010',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/task-agent-api/, ''),
      },
    },
  },
})
