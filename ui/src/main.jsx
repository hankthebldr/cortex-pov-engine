import React, { Suspense, lazy } from 'react'
import ReactDOM from 'react-dom/client'
import AppConsole from './AppConsole.jsx'
import './styles/cortex-tokens.css'
import './styles/cortex-theme.css'
import './styles/cortex-console.css'

// main.jsx renders exactly ONE root per session. It used to STATICALLY import
// both — the console AND the legacy ?theme=legacy App — so every console
// visitor also downloaded the entire legacy UI (9 legacy-only components,
// ~54 kB) they never render, folded into the entry chunk.
//
// The console is the default root ~every session renders, so it stays a STATIC
// import: it rides the entry chunk and is fetched immediately, with no "load a
// shim, then fetch the real root" serial round-trip (a real cost on the
// high-latency customer networks a POV runs on). Only the legacy App — the
// rarely-used escape hatch — is lazy: its tree downloads solely when a session
// opts into ?theme=legacy, and NEVER for a console session.
//
// Making legacy App lazy also drops the last STATIC import of EalConsole
// (App.jsx now import()s it too), so Rollup finally gives EalConsole its own
// shared chunk — no dual-import build warning.
// Guarded by src/app/__tests__/entryCodeSplit.test.js.
const App = lazy(() => import('./App.jsx'))
// Destination stylesheets are colocated with their components (see
// TenantManager.jsx, EalConsole.jsx, ToolAdapterCatalog.jsx, LabView.jsx,
// ReadinessView.jsx) so each ships inside that surface's own lazy chunk
// instead of loading on first paint for a destination the session may
// never open. Verified zero duplicate selectors across the 13 destination
// sheets, so there is no load-order hazard from moving these five.

/**
 * Theme router — URL flag selects the shell.
 *
 *   (default)          → Mission Ops Console (dark, operator-first)
 *   ?theme=legacy      → previous light-themed App (kept as an escape hatch
 *                        during the soak period — see migration step 9)
 *
 * The choice is persisted in localStorage under `cortexsim.theme` so a DC
 * who explicitly opts in to either theme keeps it across reloads.
 *
 * To force the default for an existing browser:
 *   localStorage.removeItem('cortexsim.theme')   // or visit ?theme=console
 */
function resolveTheme() {
  try {
    const url = new URL(window.location.href)
    const urlTheme = url.searchParams.get('theme')
    if (urlTheme === 'console' || urlTheme === 'legacy') {
      window.localStorage.setItem('cortexsim.theme', urlTheme)
      return urlTheme
    }
    const stored = window.localStorage.getItem('cortexsim.theme')
    if (stored === 'console' || stored === 'legacy') return stored
  } catch {
    /* non-browser context */
  }
  return 'console'
}

const theme = resolveTheme()
const Root = theme === 'console' ? AppConsole : App

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <Suspense
      fallback={
        <div
          role="status"
          aria-live="polite"
          style={{ padding: '2rem', font: '14px system-ui, sans-serif', opacity: 0.6 }}
        >
          loading…
        </div>
      }
    >
      <Root />
    </Suspense>
  </React.StrictMode>
)
