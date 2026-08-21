/**
 * Identity resolution — the fix for the console's HTTP 422 launch failure.
 *
 * Every fixture here carries BOTH identities, exactly as SimCore's
 * `Agent.to_dict()` / `Run.to_dict()` emit them. The old ordering
 * (`a.id || a.agent_id`) passed these fixtures' *integer PK* to an API that
 * requires the string, which is why the bug survived a green suite: the old
 * fixtures only ever carried one key.
 */
import { describe, it, expect } from 'vitest'
import { agentIdOf, runIdOf, scenarioIdOf, idMatches } from '../../api/ids.js'

// Verbatim shape of GET /api/agents (core/models.py Agent.to_dict).
const API_AGENT = {
  id: 1,
  agent_id: 'jumpbox-01',
  hostname: 'ip-10-0-1-24',
  os: 'linux',
  status: 'online',
}

// Verbatim shape of GET /api/runs (core/models.py Run.to_dict).
const API_RUN = {
  id: 7,
  run_id: '4ee09860-140d-4078-bd20-5179f5dbf7af',
  scenario_id: 'SIM-EDR-001',
  status: 'failed',
}

describe('agentIdOf', () => {
  it('returns the string agent_id, never the integer PK', () => {
    expect(agentIdOf(API_AGENT)).toBe('jumpbox-01')
  })

  it('produces a value the API accepts as target_agent_id (a string)', () => {
    expect(typeof agentIdOf(API_AGENT)).toBe('string')
  })

  it('falls back to the PK for records that carry no agent_id', () => {
    expect(agentIdOf({ id: 3 })).toBe('3')
  })

  it('is null-safe', () => {
    expect(agentIdOf(null)).toBeNull()
    expect(agentIdOf(undefined)).toBeNull()
    expect(agentIdOf({})).toBeNull()
  })
})

describe('runIdOf', () => {
  it('returns the uuid run_id — the key every /api/runs/{id} path uses', () => {
    expect(runIdOf(API_RUN)).toBe('4ee09860-140d-4078-bd20-5179f5dbf7af')
  })

  it('falls back to the PK when run_id is absent', () => {
    expect(runIdOf({ id: 7 })).toBe('7')
  })

  it('is null-safe', () => {
    expect(runIdOf(null)).toBeNull()
  })
})

describe('scenarioIdOf', () => {
  it('prefers scenario_id', () => {
    expect(scenarioIdOf({ id: 12, scenario_id: 'SIM-EDR-001' })).toBe('SIM-EDR-001')
  })
})

describe('idMatches', () => {
  it('matches a router param (always a string) against a numeric fallback id', () => {
    expect(idMatches(runIdOf({ id: 7 }), '7')).toBe(true)
  })

  it('matches uuid run ids', () => {
    expect(idMatches(runIdOf(API_RUN), API_RUN.run_id)).toBe(true)
  })

  it('does not match unrelated ids', () => {
    expect(idMatches('jumpbox-01', 'jumpbox-02')).toBe(false)
  })

  it('never matches on null', () => {
    expect(idMatches(null, null)).toBe(false)
    expect(idMatches('a', null)).toBe(false)
  })
})
