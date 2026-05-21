import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import AppConsole from './AppConsole.jsx'
import './styles/cortex-theme.css'
import './styles/cortex-console.css'

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
    <Root />
  </React.StrictMode>
)
