/**
 * The TTP card sheet and its detection deep dive — design v33 C10.
 *
 * Both open with "Why this matters" (Thesis · Why we simulate it · What proves
 * it · What it needs), read off the card document GET /api/ttps/{id} serves.
 * A part the card does not carry says "Not written for this card yet." The
 * sheet's actions are verbs from the closed set, each with its icon; the
 * Navigator layer is a file, not a verb, so it reads as a link.
 *
 * The card is trimmed from detection_scanner/ttps/TTP-2026-0061 (FTP cleartext
 * egress).
 */
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import TtpBrowserView from '../console/TtpBrowserView.jsx'
import { installRoutes } from '../../test/mockFetch.js'
import { VERBS, VERB_ICONS } from '../console/kit/index.js'

void React

const LIST = {
  ttps: [{
    id: 'TTP-2026-0061',
    name: 'FTP Cleartext Egress + Credential Exposure',
    status: 'active',
    summary: 'A non-admin endpoint opens an outbound FTP control session.',
    tags: [], platforms: ['linux'], technique_ids: ['T1071'], tactic_ids: ['TA0011'],
    actor_names: [], detection_counts: { iocs: 0, biocs: 1, xql_queries: 1, correlation_rules: 1, analytics_modules: 0 },
  }],
  total: 1,
}

const CARD = {
  id: 'TTP-2026-0061',
  status: 'active',
  metadata: { tags: [] },
  identity: {
    name: 'FTP Cleartext Egress + Credential Exposure',
    summary: 'A non-admin endpoint opens an outbound FTP control session.',
    description: 'FTP remains a favored exfiltration channel precisely because it is unencrypted. The control channel carries credentials in the clear.',
  },
  threat_context: { actors: [] },
  mitre_attack: {
    techniques: [
      { technique_id: 'T1071', name: 'Application Layer Protocol', tactic_ids: ['TA0011'] },
      { technique_id: 'T1048', subtechnique_id: 'T1048.003', name: 'Exfiltration Over Unencrypted Non-C2 Protocol', tactic_ids: ['TA0010'] },
    ],
  },
  execution: {
    target_platform: 'linux',
    privilege_required: 'user',
    prerequisites: [{ description: 'NGFW forwards outbound traffic logs into XSIAM.' }],
  },
  detections: {
    iocs: [],
    biocs: [{
      name: 'NGFW EAL outbound FTP STOR file transfer with data payload',
      description: 'NGFW EAL — outbound file transfer via FTP STOR command + 4 KB data payload',
      logic: 'dataset = panw_ngfw_traffic_raw\n| filter app = "ftp"',
      severity: 'high',
      mitre_technique_ids: ['T1048.003'],
    }],
    xql_queries: [{
      purpose: 'validation',
      name: 'NGFW App-ID FTP outbound session from non-admin endpoint',
      query: 'dataset = panw_ngfw_traffic_raw\n| filter app = "ftp"',
      expected_rows_min: 1,
      dataset: 'panw_ngfw_traffic_raw',
    }],
    correlation_rules: [{
      name: 'FTP Cleartext Egress — Credential Exposure Plus STOR Upload',
      rule_id: 'CR-NDR-0005',
      logic: 'BIOC(NGFW EAL outbound FTP STOR file transfer with data payload)',
    }],
    analytics_modules: [],
  },
  panw_mapping: {
    products: [],
    threat_scenarios: [{
      name: 'Cleartext FTP Exfiltration Detection via NGFW EAL',
      description: 'Validate that NGFW App-ID detects outbound FTP with cleartext credentials.',
      threat_steps: [
        {
          objective: 'Outbound FTP App-ID is detected from NGFW telemetry.',
          success_criteria: ['XQL `NGFW App-ID FTP outbound session from non-admin endpoint` returns >= 1 row.'],
        },
        {
          objective: 'The STOR upload stitches into a single exfiltration incident.',
          success_criteria: [
            'BIOC `NGFW EAL outbound FTP STOR file transfer with data payload` returns >= 1 row.',
            'Correlation `CR-NDR-0005` raises a single data-exfiltration incident.',
          ],
        },
      ],
    }],
  },
  references: [],
  referenced_by_adapters: [],
}

async function openCard(card = CARD) {
  installRoutes({
    'GET /api/ttps': LIST,
    'GET /api/ttps/TTP-2026-0061': card,
    'GET /api/ttps/TTP-2026-0061/runs': { ttp_id: 'TTP-2026-0061', runs: [], total: 0 },
  })
  render(<TtpBrowserView initialTtpId="TTP-2026-0061" />)
  return screen.findByTestId('ttp-why-matters')
}

const partsOf = (section) => within(section).getAllByRole('listitem')

describe('TTP card sheet — why this matters', () => {
  it('opens with the four parts read off the card, before its summary', async () => {
    const why = await openCard()
    expect(within(why).getByRole('heading', { level: 4, name: 'Why this matters' })).toBeInTheDocument()
    const parts = partsOf(why)
    expect(parts.map((li) => li.getAttribute('data-part'))).toEqual(['thesis', 'why', 'proves', 'needs'])
    expect(parts.map((li) => li.querySelector('.why-matters__label').textContent))
      .toEqual(['Thesis', 'Why we simulate it', 'What proves it', 'What it needs'])
    expect(parts[0]).toHaveTextContent('FTP remains a favored exfiltration channel precisely because it is unencrypted.')
    expect(parts[0]).not.toHaveTextContent('carries credentials in the clear')
    expect(parts[1]).toHaveTextContent('Validate that NGFW App-ID detects outbound FTP with cleartext credentials.')
    expect(parts[2]).toHaveTextContent('Outbound FTP App-ID is detected from NGFW telemetry.')
    expect(parts[2]).toHaveTextContent('The STOR upload stitches into a single exfiltration incident.')
    expect(parts[3]).toHaveTextContent('Target: linux; privilege: user.')
    expect(parts[3]).toHaveTextContent('NGFW forwards outbound traffic logs into XSIAM.')
    // Before the summary in reading order; the summary keeps its own section.
    const summary = screen.getByText(CARD.identity.summary)
    expect(why.compareDocumentPosition(summary) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('a card that does not carry a part says so plainly', async () => {
    const why = await openCard({
      ...CARD,
      identity: { name: CARD.identity.name, summary: CARD.identity.summary },
      panw_mapping: { products: [] },
    })
    const unwritten = partsOf(why).filter((li) => li.getAttribute('data-written') === 'false')
    expect(unwritten.map((li) => li.getAttribute('data-part'))).toEqual(['thesis', 'why', 'proves'])
    for (const li of unwritten) expect(li).toHaveTextContent('Not written for this card yet.')
    // The summary is never borrowed as the thesis.
    expect(screen.getAllByText(CARD.identity.summary)).toHaveLength(1)
  })
})

describe('TTP card sheet — actions', () => {
  it('its buttons are verbs from the closed set, each with its icon', async () => {
    await openCard()
    const row = screen.getByTestId('ttp-detail-actions')
    const verbs = within(row).getAllByRole('button').filter((b) => b.classList.contains('kit-btn'))
    expect(verbs.map((b) => b.textContent)).toEqual(['Add to chain', 'Run', 'Customize'])
    for (const b of verbs) {
      expect(VERBS).toContain(b.textContent)
      expect(b).toHaveAttribute('data-verb', b.textContent)
      expect(b.querySelector('svg')).toHaveAttribute('data-icon', VERB_ICONS[b.textContent])
    }
    // Launch-all and Edit kept their hooks under their verbs.
    expect(screen.getByTestId('ttp-launch-all')).toHaveTextContent('Run')
    expect(screen.getByTestId('ttp-edit')).toHaveTextContent('Customize')
  })

  it('the Navigator layer is not dressed as an action: it reads as a link', async () => {
    await openCard()
    const layer = screen.getByTestId('ttp-export-navigator')
    expect(layer).toHaveClass('sheet-link')
    expect(layer).not.toHaveClass('kit-btn')
    expect(layer).not.toHaveClass('btn')
    expect(layer).not.toHaveAttribute('data-verb')
    expect(layer).toHaveTextContent('ATT&CK Navigator layer ↓')
  })
})

describe('Detection deep dive — why this matters', () => {
  it('an opened BIOC starts with its four parts, its description and proof read off the card', async () => {
    await openCard()
    expect(screen.queryByTestId('ttp-det-why-biocs-0')).toBeNull()
    fireEvent.click(screen.getByTestId('ttp-det-biocs-0'))
    const why = screen.getByTestId('ttp-det-why-biocs-0')
    expect(within(why).getByRole('heading', { level: 5, name: 'Why this matters' })).toBeInTheDocument()
    const parts = partsOf(why)
    expect(parts.map((li) => li.getAttribute('data-part'))).toEqual(['thesis', 'why', 'proves', 'needs'])
    expect(parts[0]).toHaveTextContent('NGFW EAL — outbound file transfer via FTP STOR command + 4 KB data payload.')
    expect(parts[1]).toHaveTextContent('The STOR upload stitches into a single exfiltration incident.')
    expect(parts[1]).toHaveTextContent('Covers T1048.003 Exfiltration Over Unencrypted Non-C2 Protocol.')
    expect(parts[2]).toHaveTextContent('BIOC `NGFW EAL outbound FTP STOR file transfer with data payload` returns >= 1 row.')
    expect(parts[3]).toHaveTextContent('Must exist in the tenant before the run.')
    expect(parts[3]).toHaveTextContent('Reads dataset panw_ngfw_traffic_raw.')
    // It opens the detection: the body (the rule logic) follows it.
    const body = screen.getByTestId('ttp-det-body-biocs-0')
    expect(why.compareDocumentPosition(body) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('a correlation rule with no description says its thesis is not written', async () => {
    await openCard()
    fireEvent.click(screen.getByTestId('ttp-det-correlation_rules-0'))
    const why = screen.getByTestId('ttp-det-why-correlation_rules-0')
    const thesis = why.querySelector('[data-part="thesis"]')
    expect(thesis).toHaveAttribute('data-written', 'false')
    expect(thesis).toHaveTextContent('Not written for this detection yet.')
    expect(why.querySelector('[data-part="proves"]'))
      .toHaveTextContent('Correlation `CR-NDR-0005` raises a single data-exfiltration incident.')
  })
})
