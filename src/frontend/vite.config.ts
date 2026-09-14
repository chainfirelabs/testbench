import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// This file runs in Node, but the project carries no @types/node — the app
// itself is browser-only and does not need them. Declaring the one global used
// here is cheaper than a dependency that exists for a single line.
declare const process: { env: Record<string, string | undefined> }

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      // Defaults to a backend run directly (uvicorn on 8000). Set
      // TB_API_TARGET to point at another one — docker compose publishes the
      // backend on 127.0.0.1:8001, so `TB_API_TARGET=http://localhost:8001`
      // runs the dev server against the compose stack.
      '/api': process.env.TB_API_TARGET || 'http://localhost:8000',
    },
  },
})
