import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import AppConsole from './AppConsole.jsx'
import './styles/cortex-theme.css'
import './styles/cortex-console.css'

/**
 * Theme router — URL flag selects the shell.
 *
 *   ?theme=console     → Mission Ops Console (new)
 *   (default)          → legacy light theme
 *
 * The choice is also persisted in localStorage under `cortexsim.theme` so
 * DCs who prefer the console only set the flag once.
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
  return 'legacy'
}

const theme = resolveTheme()
const Root = theme === 'console' ? AppConsole : App

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>
)
