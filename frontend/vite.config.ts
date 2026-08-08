import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Dev-only proxy to the real FastAPI backend (python -m uvicorn
// backend.api.main:app --port 8000, see RunCommand.txt). The backend has no
// CORS middleware — test_ui.html gets away with relative fetch('/vendors')
// paths because FastAPI serves it same-origin at "/"; this proxy keeps the
// Vite dev app's relative api/* calls same-origin too, matching that
// convention instead of adding CORS headers server-side. Override the
// target with VITE_API_BASE if the backend runs on a different port.
const API_BASE = process.env.VITE_API_BASE || 'http://localhost:8000'
const API_PATH_PREFIXES = [
  '/vendors', '/models', '/plan-allocations', '/plan-runs', '/payments',
  '/master-data', '/config', '/audit-log', '/ai', '/ingestion', '/rollover', '/calendar', '/analytics',
]

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: Object.fromEntries(API_PATH_PREFIXES.map((p) => [p, { target: API_BASE, changeOrigin: true }])),
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: true,
  },
})
