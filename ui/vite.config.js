import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    rollupOptions: {
      output: {
        // React/ReactDOM change far less often than app code and are shared
        // by every route — their own chunk means a redeploy that only
        // touches app code doesn't invalidate the framework's browser cache.
        // Destination-level splitting (see src/app/destinations.jsx, which
        // now lazy-imports each of the console's 14 surfaces) does the rest
        // of the work: Rollup gives each lazily-imported surface its own
        // chunk automatically, no manualChunks entry needed per-destination.
        manualChunks: {
          'vendor-react': ['react', 'react-dom'],
        },
      },
    },
  },
  server: {
    proxy: {
      // Point the dev server at a SimCore on another port with
      // CORTEXSIM_DEV_API=http://localhost:8899 — useful when the compose stack
      // on 8888 is an older build and you need to drive the UI against a
      // freshly built image.
      '/api': process.env.CORTEXSIM_DEV_API || 'http://localhost:8888'
    }
  }
})
