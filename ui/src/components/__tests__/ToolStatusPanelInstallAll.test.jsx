/**
 * Legacy tool panel — Install All (RG-GUARD-08).
 *
 * Round 1 made SimCore run one tool install at a time and answer
 * 409 INSTALL_IN_PROGRESS for a second request. Install All awaited each
 * install with the console's default 20 s request budget, swallowed every
 * error, and ended with the toast "All tools installed". A real install (a pip
 * or cargo build, capped at 600 s server-side) outlasts 20 s, so the panel gave
 * up on tool 1, asked for tool 2, got 409, swallowed it — and announced success
 * having installed one tool.
 *
 * The panel is mounted only in the legacy UI (`?theme=legacy`).
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import ToolStatusPanel, { INSTALL_RETRY_MS } from '../ToolStatusPanel.jsx'
import { installTool, INSTALL_TIMEOUT_MS, REQUEST_TIMEOUT_MS } from '../../api/client.js'

void React

const TOOLS = [
  { tool_name: 'mocktaxii', status: 'not_installed' },
  { tool_name: 'gocortexbrokenbank', status: 'not_installed' },
]
const busy = (name, secs) => new Response(JSON.stringify({
  detail: { error: 'Another tool install is already running', code: 'INSTALL_IN_PROGRESS', detail: `'${name}' has been installing for ${secs}s; installs run one at a time.` },
}), { status: 409, headers: { 'content-type': 'application/json' } })

afterEach(() => { vi.useRealTimers() })

const flush = async (ms = 0) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms) }) }

describe('installTool — a build is given a build\'s time', () => {
  it('waits past the default request budget: the install cap is 600 s server-side', async () => {
    expect(INSTALL_TIMEOUT_MS).toBeGreaterThan(600_000)
    vi.useFakeTimers()
    globalThis.fetch = vi.fn((_url, init) => new Promise((resolve, reject) => {
      const t = setTimeout(() => resolve(new Response(JSON.stringify({ status: 'installed', tool_name: 'mocktaxii' }), {
        status: 200, headers: { 'content-type': 'application/json' },
      })), REQUEST_TIMEOUT_MS + 5_000)
      init.signal?.addEventListener('abort', () => { clearTimeout(t); reject(new DOMException('aborted', 'AbortError')) })
    }))
    const p = installTool('mocktaxii')
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS + 6_000)
    await expect(p).resolves.toMatchObject({ status: 'installed' })
  })
})

describe('ToolStatusPanel — Install All tells the truth about each tool', () => {
  async function clickInstallAll(onMessage) {
    render(<ToolStatusPanel onMessage={onMessage} />)
    await flush()
    fireEvent.click(screen.getByRole('button', { name: /Install All/i }))
  }
  const finals = (onMessage) => onMessage.mock.calls.filter(([, type]) => type === 'success' || type === 'error')

  it('waits out 409 INSTALL_IN_PROGRESS and retries that tool instead of skipping it', async () => {
    vi.useFakeTimers()
    const calls = { mocktaxii: 0, gocortexbrokenbank: 0 }
    installRoutes({
      'GET /api/tools': { tools: TOOLS },
      'POST /api/tools/mocktaxii/install': () => { calls.mocktaxii += 1; return { status: 'installed', tool_name: 'mocktaxii' } },
      'POST /api/tools/gocortexbrokenbank/install': () => {
        calls.gocortexbrokenbank += 1
        return calls.gocortexbrokenbank < 3 ? busy('mocktaxii', 4) : { status: 'installed', tool_name: 'gocortexbrokenbank' }
      },
    })
    const onMessage = vi.fn()
    await clickInstallAll(onMessage)
    await flush()
    // Refused twice: it must still be waiting, not announcing.
    expect(finals(onMessage)).toHaveLength(0)
    await flush(INSTALL_RETRY_MS)
    await flush(INSTALL_RETRY_MS)
    expect(calls.gocortexbrokenbank).toBe(3)
    expect(finals(onMessage)).toEqual([['All 2 tools installed', 'success']])
  })

  it('a tool that failed is named with its reason, and the toast is not a success', async () => {
    vi.useFakeTimers()
    installRoutes({
      'GET /api/tools': { tools: TOOLS },
      'POST /api/tools/mocktaxii/install': () => new Response(JSON.stringify({
        detail: { error: 'Install failed', code: 'INSTALL_FAILED', detail: 'pip exited 1' },
      }), { status: 500, headers: { 'content-type': 'application/json' } }),
      'POST /api/tools/gocortexbrokenbank/install': { status: 'installed', tool_name: 'gocortexbrokenbank' },
    })
    const onMessage = vi.fn()
    await clickInstallAll(onMessage)
    await flush()
    const done = finals(onMessage)
    expect(done).toHaveLength(1)
    const [text, type] = done[0]
    expect(type).toBe('error')
    expect(text).toMatch(/1 of 2/)
    expect(text).toContain('mocktaxii')
    expect(text).toMatch(/Install failed/)
    expect(text).not.toMatch(/All .*tools installed/)
  })

  it('a tool still refused after the wait budget is reported as not installed', async () => {
    vi.useFakeTimers()
    installRoutes({
      'GET /api/tools': { tools: [TOOLS[0]] },
      'POST /api/tools/mocktaxii/install': () => busy('ackbarx', 9),
    })
    const onMessage = vi.fn()
    await clickInstallAll(onMessage)
    await flush(INSTALL_TIMEOUT_MS + INSTALL_RETRY_MS)
    const done = finals(onMessage)
    expect(done).toHaveLength(1)
    expect(done[0][1]).toBe('error')
    expect(done[0][0]).toContain('INSTALL_IN_PROGRESS')
  })
})
