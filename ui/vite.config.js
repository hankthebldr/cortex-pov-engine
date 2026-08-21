import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
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
