import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev server proxies /api to the FastAPI backend on :8000, and /pos to the
// retail shop it mounts there — same-origin in dev as in a build, which is what
// keeps the shop's login cookie working inside the POS frame.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
      '/pos': 'http://localhost:8000',
    },
  },
  build: { outDir: 'dist' },
})
