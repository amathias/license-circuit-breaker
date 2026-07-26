import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The built console is served by FastAPI from web/dist, mounted at "/". A
// relative base keeps it working behind the coordinator's reverse proxy, which
// may mount the project under a path prefix rather than at a domain root.
export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // One bundle. The console is small, and a judge on a cold cache should not
    // wait on a waterfall of chunk requests during a recorded demo.
    chunkSizeWarningLimit: 900,
  },
  server: {
    port: 5173,
    proxy: {
      // Dev only. In production the API and the console share an origin.
      '/api': {
        target: 'http://127.0.0.1:8102',
        changeOrigin: false,
      },
    },
  },
})
