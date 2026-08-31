import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.js'],
    // `css: false` (empty `include`) keeps the main suite fast — CSS
    // imports are stubbed to empty strings everywhere EXCEPT the specific
    // stylesheets the contrast guard (src/styles/__tests__/console-contrast
    // .test.jsx) actually needs real cascade/selector behavior from. That
    // guard is the one place in this repo that renders CSS —
    // see its file doc for why (a 36-agent redesign shipped invisible
    // page titles behind 701 green tests specifically because nothing
    // ever rendered a stylesheet). Scoping `include` to just those files
    // means the rest of the suite pays no CSS-processing cost, without
    // needing a second vitest project/config to get `css: true` (Vitest
    // 1.x workspace files are not auto-discovered by a bare `vitest run`,
    // so a second-project split would silently not run under CI's/this
    // repo's actual invocation — this stays inside the one config that is).
    css: {
      include: [
        /\/styles\/cortex-tokens\.css$/,
        /\/styles\/cortex-theme\.css$/,
        /\/styles\/cortex-console\.css$/,
        /\/styles\/destinations\/(uctc|ttps|readiness|eal|adapters)\.css$/,
      ],
    },
    include: ['src/**/__tests__/**/*.test.{js,jsx}'],
    exclude: ['node_modules', 'dist', 'tests/e2e'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov', 'html'],
      include: ['src/**/*.{js,jsx}'],
      exclude: [
        'src/main.jsx',
        'src/**/__tests__/**',
        'src/test/**',
      ],
      thresholds: {
        // Conservative baseline — raise once full coverage lands.
        lines: 35,
        functions: 35,
        statements: 35,
        branches: 50,
      },
    },
  },
})
