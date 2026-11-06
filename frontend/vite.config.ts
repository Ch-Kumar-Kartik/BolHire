import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Same-origin API in development: the browser only talks to the Vite origin, so the session cookie needs no cross-origin handling
// (no CORS credentials). The /api path is preserved and the Origin header is forwarded unchanged; it must equal AUTH_TRUSTED_ORIGIN.
// 127.0.0.1, not localhost: Node may resolve localhost to ::1 while uvicorn listens on IPv4.
const backend = { '/api': { target: 'http://127.0.0.1:8000' } }

export default defineConfig({
  plugins: [react()],
  server: { proxy: backend },
  preview: { proxy: backend },
})
