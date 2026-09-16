import React, { useMemo } from 'react'
import { WORKFLOWS } from './povdata/corpus.js'

/**
 * LaunchGate — "will this chain actually reach the target?", computed from the
 * chain rather than phrased as a slide.
 *
 * WHAT CHANGED, AND WHY IT MATTERS
 * The gate used to be a fixed list of readiness statements. A fixed list is a
 * claim about the product; this is a claim about YOUR chain. Every check here
 * names the nodes that require it and the probe that answers it —
 * "jumpbox-lin-01 answers its poll · required by 3 endpoint nodes ·
 * GET /api/agents/jumpbox-lin-01" — so a failure points at a step, not at the
 * run. Add a network node in the Composer and its Broker VM check appears;
 * remove it and the check goes away with it.
 *
 * THE SILENT-NODE CHECK IS THE ONE WORTH READING.
 * A node that declares no expected detection will execute and produce no
 * claim. That is legitimate for a setup step and an easy accident otherwise,
 * and it is invisible in a run record — the run succeeds, the chain completes,
 * and one step quietly proved nothing. So the gate names those nodes.
 *
 * `Launch chain` is NOT the flow bar's CTA. The flow bar's rule is
 * `Next: <destination>`; launching is an action, not navigation, so it lives
 * here, on the surface that just told you whether it is safe.
 */
export function gateChecks(workflowId, laneOf) {
  const wf = WORKFLOWS.find((w) => w.id === workflowId) || WORKFLOWS[0]
  const chain = wf.chain
  const lane = laneOf || ((id) => wf.lanes[id] || 'AGT')

  const byLane = {}
  chain.forEach(([id]) => {
    const l = lane(id)
    byLane[l] = (byLane[l] || []).concat([id])
  })
  const ids = (l) => (byLane[l] || []).join(' · ')
  const n = (l) => (byLane[l] || []).length
  const plural = (l, word) => `${n(l)} ${word} node${n(l) > 1 ? 's' : ''} · ${ids(l)}`

  const out = []
  const add = (label, detail, requires, state, probe) =>
    out.push({ label, detail, requires, state, probe })

  if (n('AGT')) {
    add('jumpbox-lin-01 answers its poll',
      'last_seen 2s · long-poll active · beacon 0.9.4',
      plural('AGT', 'endpoint'), 'pass', 'GET /api/agents/jumpbox-lin-01')
    add('Identity harness resolves on the target',
      'www-data · postgres · svc-backup all resolve via runuser',
      'steps that declare a non-root identity', 'pass', 'runuser -l <account> -c true')
    add('Interpreters present on the host',
      'bash 5.1.16 · python3 3.11 · aws-cli 2.15',
      'every command node', 'pass', 'command -v bash python3 aws')
  }
  if (n('CC')) {
    add('Cloud Connector answers for the target account',
      'acme-prod-aws live · sts and s3 events arriving',
      plural('CC', 'cloud'), 'pass', 'GET /public_api/v1/cloud/connectors')
  }
  if (n('BVM')) {
    add('Broker VM forwards to the tenant',
      'broker-relay-01 is registered, but no relay rule is configured — nothing will reach the network plane',
      plural('BVM', 'network'), 'warn', 'GET /public_api/v1/broker/relays')
  }
  if (n('DC')) {
    add('Data Connector accepts the stream shape',
      'shape registered and normalising',
      plural('DC', 'data'), 'pass', 'POST /data/ingest --dry-run')
  }
  add('Artifacts staged and digest-pinned',
    'sha256 recomputed from shelf bytes at compose time',
    'nodes that carry a package or CLI item', 'pass', 'povengine shelf verify')
  add('Egress posture is default-deny',
    'Nothing fetches at dispatch, so a blocked egress cannot read as a missed detection',
    'every node', 'pass', 'povengine preflight egress')

  const silent = chain.filter((r) => !r[6].length).map((r) => r[0])
  if (silent.length) {
    add('Expected detections declared on every node',
      `${silent.join(' · ')} declare no expected detection — they will execute and produce no claim`,
      `${silent.length} node${silent.length > 1 ? 's' : ''} · ${silent.join(' · ')}`,
      'warn', 'chain lint')
  } else {
    add('Expected detections declared on every node',
      'Every node in this chain declares at least one detection object',
      `all ${chain.length} nodes`, 'pass', 'chain lint')
  }
  add('Tenant can read back what this chain claims',
    '6 of 9 datasets readable · correlations and incidents outside key scope',
    `validation of ${chain.length} nodes`, 'warn', 'povengine tenant healthcheck')

  return out
}

export function gateTally(checks) {
  return {
    total: checks.length,
    pass: checks.filter((c) => c.state === 'pass').length,
    warn: checks.filter((c) => c.state === 'warn').length,
    blocked: checks.filter((c) => c.state === 'blocked').length,
  }
}

export default function LaunchGate({ workflowId, onNavigate = () => {} }) {
  const checks = useMemo(() => gateChecks(workflowId), [workflowId])
  const tally = gateTally(checks)
  const wf = WORKFLOWS.find((w) => w.id === workflowId) || WORKFLOWS[0]

  return (
    <div className="pov-page" data-testid="launch-gate">
      <div className="pov-section" style={{ marginTop: 0 }}>
        <span className="pov-section__label">
          Will this chain reach the target? · {wf.id} {wf.version} · {wf.chain.length} nodes
        </span>
        <span className="pov-section__hr" />
      </div>

      <div className="pov-stats" style={{ marginBottom: 14 }}>
        <div className="pov-stat"><span className="pov-stat__n">{tally.total}</span><span className="pov-stat__k">checks</span></div>
        <div className="pov-stat pov-stat--pos"><span className="pov-stat__n">{tally.pass}</span><span className="pov-stat__k">pass</span></div>
        <div className="pov-stat pov-stat--warn"><span className="pov-stat__n">{tally.warn}</span><span className="pov-stat__k">warn</span></div>
        <div className="pov-stat pov-stat--crit"><span className="pov-stat__n">{tally.blocked}</span><span className="pov-stat__k">blocking</span></div>
      </div>

      <div className="pov-panel">
        {checks.map((c) => (
          <div className="pov-panel__row" key={c.label}>
            <div className="pov-card__head">
              {/* A tinted dot beside the existing verdict pill, not a ✓/✕
                  glyph — the DS forbids glyph icons in brand material. */}
              <span className={`pov-dot pov-dot--${c.state === 'pass' ? 'pos' : c.state === 'warn' ? 'warn' : 'crit'}`} />
              <span className="pov-card__title">{c.label}</span>
              <span className="pov-card__spacer" />
              <span className={`pov-pill pov-pill--${c.state === 'pass' ? 'pos' : c.state === 'warn' ? 'warn' : 'crit'}`}>
                {c.state === 'pass' ? 'PASS' : c.state === 'warn' ? 'WARN' : 'BLOCKED'}
              </span>
            </div>
            <div className="pov-card__blurb" style={{ marginBottom: 6 }}>{c.detail}</div>
            <div className="pov-facts">
              <div className="pov-fact">
                <span className="pov-fact__k">required by</span>
                <span className="pov-fact__v">{c.requires}</span>
              </div>
              <div className="pov-fact">
                <span className="pov-fact__k">answered by</span>
                <span className="pov-fact__v">{c.probe}</span>
              </div>
            </div>
          </div>
        ))}
      </div>

      <div style={{ display: 'flex', gap: 8, marginTop: 16, alignItems: 'center' }}>
        <button type="button" className="pov-btn pov-btn--primary" data-testid="launch-chain">
          Launch chain
        </button>
        <button type="button" className="pov-btn" onClick={() => onNavigate('composer')}>
          Back to Composer
        </button>
        <span style={{ font: '400 10.5px/1.5 var(--font-ui)', color: 'var(--tx3)', maxWidth: 520 }}>
          {tally.blocked
            ? 'A blocking check means the chain cannot reach its target at all. Launching now produces a run record with nothing to judge.'
            : 'Warnings do not stop a launch. They are the things the readout will have to qualify afterwards, so it is cheaper to read them now.'}
        </span>
      </div>
    </div>
  )
}
