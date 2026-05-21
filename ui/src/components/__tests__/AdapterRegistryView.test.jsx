/**
 * Smoke + interaction tests for the EAL adapter registry browser.
 */
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import AdapterRegistryView from '../console/AdapterRegistryView.jsx'
import { installRoutes } from '../../test/mockFetch.js'

void React

const fixturePluginList = {
  plugins: [
    {
      name: 'c2_http_beacon',
      version: '1.0.0',
      description: 'HTTPS beacon C2 traffic simulator',
      mitre_techniques: ['T1071.001'],
      eal_targets: ['ndr'],
    },
    {
      name: 'airs_prompt_attack',
      version: '0.3.0',
      description: 'Prompt-injection probe runner against AIRS-protected LLMs',
      mitre_techniques: ['T1059', 'T1071'],
      eal_targets: ['airs'],
    },
    {
      name: 'oauth_grant_emulator',
      version: '1.0.0',
      description: 'OAuth 2.0 risky-grant flow for Cloud App detection',
      mitre_techniques: ['T1078.004'],
      eal_targets: ['cloud_app'],
    },
  ],
  total: 3,
}

const fixturePluginDetail = {
  name: 'airs_prompt_attack',
  version: '0.3.0',
  description: 'Prompt-injection probe runner.\n\nMaps OWASP LLM01.',
  mitre_techniques: ['T1059', 'T1071'],
  eal_targets: ['airs'],
  params_schema: {
    title: 'AirsPromptAttackParams',
    type: 'object',
    properties: {
      target_url: {
        type: 'string',
        description: 'URL of the AIRS-protected LLM',
      },
      probes_dir: {
        type: 'string',
        description: 'Path to promptmap probe pack',
        default: 'scenarios/airs/probes/',
      },
      max_probes: {
        type: 'integer',
        description: 'Cap the number of probes fired',
        default: 50,
      },
    },
    required: ['target_url'],
  },
}

describe('<AdapterRegistryView />', () => {
  it('renders intro + stats', async () => {
    installRoutes({ 'GET /api/eal/plugins': fixturePluginList })
    render(<AdapterRegistryView />)
    await waitFor(() => {
      expect(screen.getByText(/adapters installed/i)).toBeInTheDocument()
    })
    expect(screen.getByText(/Every attack adapter that ships/i)).toBeInTheDocument()
  })

  it('renders one card per installed plugin', async () => {
    installRoutes({ 'GET /api/eal/plugins': fixturePluginList })
    render(<AdapterRegistryView />)
    await waitFor(() => {
      expect(screen.getByText('c2_http_beacon')).toBeInTheDocument()
    })
    expect(screen.getByText('airs_prompt_attack')).toBeInTheDocument()
    expect(screen.getByText('oauth_grant_emulator')).toBeInTheDocument()
  })

  it('categorizes plugins by name pattern', async () => {
    installRoutes({ 'GET /api/eal/plugins': fixturePluginList })
    const { container } = render(<AdapterRegistryView />)
    await waitFor(() => {
      expect(screen.getByText('c2_http_beacon')).toBeInTheDocument()
    })
    // Category labels appear both in the filter chip strip AND on each
    // card. Assert through the card-level class so we don't collide.
    const categoryLabels = container.querySelectorAll('.adapter-card__category')
    const texts = Array.from(categoryLabels).map((el) => el.textContent)
    expect(texts).toContain('Command & control')
    expect(texts).toContain('AI / LLM')
    expect(texts).toContain('Identity / SaaS')
  })

  it('category filter narrows the visible card list', async () => {
    installRoutes({ 'GET /api/eal/plugins': fixturePluginList })
    render(<AdapterRegistryView />)
    await waitFor(() => {
      expect(screen.getByText('c2_http_beacon')).toBeInTheDocument()
    })
    // Click "AI / LLM" filter chip
    fireEvent.click(screen.getByRole('button', { name: /^AI \/ LLM$/ }))
    expect(screen.getByText('airs_prompt_attack')).toBeInTheDocument()
    expect(screen.queryByText('c2_http_beacon')).not.toBeInTheDocument()
    expect(screen.queryByText('oauth_grant_emulator')).not.toBeInTheDocument()
  })

  it('clicking a card opens the detail panel with the params schema', async () => {
    installRoutes({
      'GET /api/eal/plugins': fixturePluginList,
      'GET /api/eal/plugins/airs_prompt_attack': fixturePluginDetail,
    })
    render(<AdapterRegistryView />)
    await waitFor(() => {
      expect(screen.getByText('airs_prompt_attack')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('airs_prompt_attack'))
    await waitFor(() => {
      expect(screen.getByText(/AirsPromptAttackParams/)).toBeInTheDocument()
    })
    expect(screen.getByText('target_url')).toBeInTheDocument()
    expect(screen.getByText('probes_dir')).toBeInTheDocument()
    expect(screen.getByText('max_probes')).toBeInTheDocument()
  })

  it('renders parameter defaults in the schema view', async () => {
    installRoutes({
      'GET /api/eal/plugins': fixturePluginList,
      'GET /api/eal/plugins/airs_prompt_attack': fixturePluginDetail,
    })
    render(<AdapterRegistryView />)
    await waitFor(() => {
      expect(screen.getByText('airs_prompt_attack')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('airs_prompt_attack'))
    await waitFor(() => {
      // String default with quotes
      expect(screen.getByText(/"scenarios\/airs\/probes\/"/)).toBeInTheDocument()
    })
    // Numeric default without quotes
    expect(screen.getByText(/default: 50/)).toBeInTheDocument()
  })

  it('handles plugin detail load failure gracefully', async () => {
    installRoutes({
      'GET /api/eal/plugins': fixturePluginList,
      // No mock for the detail endpoint → 404
    })
    render(<AdapterRegistryView />)
    await waitFor(() => {
      expect(screen.getByText('c2_http_beacon')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('c2_http_beacon'))
    await waitFor(() => {
      expect(screen.getByText(/Load failed/i)).toBeInTheDocument()
    })
  })

  it('handles plugin list load failure with a visible error', async () => {
    installRoutes({})
    render(<AdapterRegistryView />)
    await waitFor(() => {
      // The 404 surfaces as an error banner
      expect(screen.getByText(/no mock for|Failed to load/i)).toBeInTheDocument()
    })
  })
})
