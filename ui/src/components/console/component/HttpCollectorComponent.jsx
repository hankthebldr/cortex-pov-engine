import React, { useCallback, useMemo } from 'react'
import { useEnvironment } from '../../../context/EnvironmentContext.jsx'
import { preflightCollectorTarget, putCollectorTarget } from '../../../api/client.js'
import { serverDate } from '../../../api/time.js'
import ComponentPage from './ComponentPage.jsx'
import ConfigureFrame from './ConfigureFrame.jsx'
import CollectorTargetsPanel from '../CollectorTargetsPanel.jsx'
import useComponentStatus from '../useComponentStatus.js'
import { componentByKey } from '../componentModel.js'
import { useConfirmed } from '../useSetupProgress.js'

/**
 * HTTP collector — the tenant's custom HTTP log collector, where data-stream
 * scenarios send their logs (design v26 §4.2–4.3, slice 4; the lab session's
 * design docs/superpowers/specs/2026-11-12-http-collector-wizard-design.md).
 *
 * Built on what SimCore has today:
 *   - "Add it to POVengine" is a pov step that STORES values: its Test does
 *     the PUT, then the no-write preflight. There is no Save verb. The key is
 *     write-only — cleared from the page the moment it is stored.
 *   - "Send a test event" posts the one untagged canary (preflight with
 *     write_canary) and reports it as its own row.
 *   - "Confirm it arrived" has no read on this build: the arrival endpoint,
 *     the declaration and canary-tagged example events are the HTTP
 *     collector backend (spec §6), not on dev. So it shows the query to run
 *     and the DC ticks it — "You confirmed", never Passed.
 *
 * The spec's data-source declaration and its verdicts (matches · xql_only ·
 * cannot_feed · name_disputed · native_dataset) need that backend's
 * detections-by-source read; until then "Choose format and dataset" names the
 * dataset from Vendor and Product and stops there.
 */

const KEY = 'http'
const KIND = 'xsiam_http_collector'
const VERDICT = {
  accepted: { status: 'up', text: 'accepted a test record' },
  reachable: { status: 'degraded', text: 'reachable, nothing written yet' },
  refused: { status: 'down', text: 'refused the test record' },
  unreachable: { status: 'down', text: 'unreachable' },
  not_configured: { status: 'down', text: 'not configured' },
}

/** The tenant names a custom collector's dataset `<vendor>_<product>_raw`. */
export function datasetName(vendor, product) {
  const clean = (s) => String(s || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
  const v = clean(vendor)
  const p = clean(product)
  return v && p ? `${v}_${p}_raw` : null
}

function Field({ label, children }) {
  return (
    <label className="cfg-input">
      <span className="cfg-input__label">{label}</span>
      {children}
    </label>
  )
}

function preflightRows(r) {
  return (Array.isArray(r?.rungs) ? r.rungs : []).map((g) => ({
    label: g.rung,
    state: g.status === 'ok' ? 'passed' : g.status === 'skipped' ? 'cant_check' : 'failed',
    detail: [g.detail, g.code].filter(Boolean).join(' — ') || undefined,
  }))
}

export default function HttpCollectorComponent({ params = {}, setParams = () => {}, onNavigate = () => {} }) {
  const env = useEnvironment()
  const { statuses, sources, refresh } = useComponentStatus()
  const confirmed = useConfirmed()
  const meta = componentByKey(KEY)
  const targets = useMemo(
    () => (Array.isArray(sources.collectorTargets) ? sources.collectorTargets : []).filter((t) => (t.kind || KIND) === KIND),
    [sources.collectorTargets],
  )
  const defaultTarget = targets.find((t) => t.is_default) || targets[0] || null

  const steps = useMemo(() => [
    {
      id: 'cortex',
      title: 'Add the collector in Cortex',
      why: 'Search for HTTP, hover, and click Add. The collector\'s name does not route data — its Vendor and Product do (next step).',
      where: 'Cortex › Settings › Data Sources & Integrations › + Add New',
      verifier: 'you',
      fields: ({ shared }) => [['Name', shared.vendor && shared.product ? `povengine ${shared.vendor} ${shared.product}` : 'povengine <vendor> <product>']],
    },
    {
      id: 'format',
      title: 'Choose format and dataset',
      why: 'POVengine sends JSON, gzip-compressed by default to match the collector\'s Compression setting: XSIAM HTTP collectors created gzip-only reject uncompressed POSTs. A collector set to CEF, LEEF or Raw answers 500 to every record. Vendor and Product name the dataset — a record\'s own dataset field routes nothing.',
      where: 'the same dialog',
      verifier: 'you',
      fields: ({ shared }) => [
        ['Compression', 'gzip'],
        ['Log format', 'JSON'],
        ['Dataset', datasetName(shared.vendor, shared.product) || '<vendor>_<product>_raw'],
      ],
      body: ({ shared, setShared }) => (
        <div className="cfg-inputs">
          <Field label="Vendor">
            <input className="cfg-input__box" value={shared.vendor || ''} onChange={(e) => setShared({ vendor: e.target.value })} />
          </Field>
          <Field label="Product">
            <input className="cfg-input__box" value={shared.product || ''} onChange={(e) => setShared({ product: e.target.value })} />
          </Field>
        </div>
      ),
    },
    {
      id: 'key',
      title: 'Generate the key',
      why: 'Click Save & Generate Token. The key is shown once — a lost key means generating a new one. Copy the endpoint URL from the Custom Collectors page too.',
      where: 'the same dialog, then Custom Collectors',
      verifier: 'you',
      fields: [['api_key', 'copy it now'], ['URL', 'https://api-<tenant>/logs/v1/event']],
    },
    {
      id: 'add',
      title: 'Add it to POVengine',
      why: 'Test stores the URL and key as a collector target, then checks — without writing — that POVengine can reach it. The key goes to the encrypted vault and is never shown again.',
      verifier: 'pov',
      fields: ({ draft }) => [
        ['Header', 'Authorization (the bare key — a Bearer scheme gets 401)'],
        ['Compression', (draft.compress ?? true) ? 'gzip' : 'none'],
      ],
      body: ({ draft, setDraft, shared }) => (
        <div className="cfg-inputs">
          <Field label="Target name">
            <input className="cfg-input__box mono" value={draft.name ?? (shared.vendor && shared.product ? `${shared.vendor}-${shared.product}`.toLowerCase() : '')}
              onChange={(e) => setDraft({ name: e.target.value })} />
          </Field>
          <Field label="Collector URL">
            <input className="cfg-input__box mono" value={draft.url || ''} onChange={(e) => setDraft({ url: e.target.value })}
              placeholder="https://api-<tenant>/logs/v1/event" />
          </Field>
          <Field label="API key">
            <input className="cfg-input__box mono" type="password" autoComplete="new-password" value={draft.token || ''}
              onChange={(e) => setDraft({ token: e.target.value })} />
          </Field>
          <label className="cfg__tick">
            <input type="checkbox" checked={draft.compress ?? true} data-testid="hc-compress"
              onChange={(e) => setDraft({ compress: e.target.checked })} />
            <span>Send gzip-compressed, to match the collector's Compression setting</span>
          </label>
          <p className="cfg__note">XSIAM HTTP collectors created gzip-only reject uncompressed POSTs. Turn this off only for a collector created uncompressed.</p>
          <label className="cfg__tick">
            <input type="checkbox" checked={!!draft.isDefault} onChange={(e) => setDraft({ isDefault: e.target.checked })} />
            <span>Use as the default target</span>
          </label>
        </div>
      ),
      test: async ({ draft, setDraft, shared }) => {
        const name = (draft.name ?? (shared.vendor && shared.product ? `${shared.vendor}-${shared.product}`.toLowerCase() : '')).trim()
        if (!name || !(draft.url || '').trim()) {
          return { state: 'pending', detail: 'Fill in the target name and the collector URL, then Test.' }
        }
        const body = {
          kind: KIND,
          url: draft.url.trim(),
          dataset: datasetName(shared.vendor, shared.product),
          auth_header: 'Authorization',
          auth_scheme: '',
          // Sent every time: PUT replaces the target, and the API defaults it
          // off, so leaving it out would turn compression off on each Test.
          compress: draft.compress ?? true,
          is_default: !!draft.isDefault,
        }
        if (draft.token) body.token = draft.token
        const saved = await putCollectorTarget(name, body)
        // Stored: the key leaves the page now.
        setDraft({ token: '' })
        refresh()
        const warnings = Array.isArray(saved?.warnings) ? saved.warnings : []
        const pf = await preflightCollectorTarget(name, { writeCanary: false })
        refresh()
        const rows = [{ label: 'stored', state: 'passed', detail: `target ${name}` }, ...preflightRows(pf)]
        if (warnings.length) {
          return { state: 'degraded', issue: warnings[0], detail: warnings.join(' · '), rows, share: { target: name } }
        }
        if (pf?.verdict === 'reachable' || pf?.verdict === 'accepted') {
          return { state: 'passed', detail: 'Stored, and reachable from POVengine. Nothing was written to the collector.', rows, share: { target: name } }
        }
        return {
          state: 'failed',
          issue: pf?.code || pf?.verdict || 'Not reachable',
          detail: pf?.remediation || 'POVengine could not reach the collector.',
          rows,
          share: { target: name },
        }
      },
    },
    {
      id: 'send',
      title: 'Send a test event',
      why: 'Test posts one canary record. 2xx is delivered; 401 a wrong key or a disabled collector; 404 a wrong URL; 413 too large; 429 rate limited; 500 a format mismatch. Delivered is not ingested — confirm it in the next step.',
      verifier: 'pov',
      fields: ({ shared }) => [['Target', shared.target || defaultTarget?.name || 'store one in step 4']],
      command: 'printf \'{"povengine":"canary"}\' | gzip | curl -sS -o /dev/null -w \'%{http_code}\\n\' -X POST "$URL" -H "Authorization: $KEY" -H "Content-Type: application/json" -H "Content-Encoding: gzip" --data-binary @-',
      test: async ({ shared }) => {
        const name = shared.target || defaultTarget?.name
        if (!name) return { state: 'pending', detail: 'Store a collector target first (step 4).' }
        const pf = await preflightCollectorTarget(name, { writeCanary: true })
        refresh()
        const accepted = pf?.verdict === 'accepted'
        const rows = [
          { label: 'canary', state: accepted ? 'passed' : 'failed', detail: accepted ? '2xx — the collector accepted it' : [pf?.code, pf?.remediation].filter(Boolean).join(' — ') },
          { label: 'tagged examples', state: 'cant_check', detail: 'Not sent: tagging example events needs the HTTP collector backend, which this build does not have.' },
        ]
        return accepted
          ? { state: 'passed', detail: 'The collector accepted the canary. Delivered is not ingested — confirm it in the next step.', rows }
          : { state: 'failed', issue: pf?.code || pf?.verdict || 'Refused', detail: pf?.remediation || 'The collector did not accept the canary.', rows }
      },
    },
    {
      id: 'arrived',
      title: 'Confirm it arrived',
      why: 'Search the dataset for the canary. POVengine cannot run this read on this build, so run it in Cortex and tick the step. It counts every canary ever sent to the dataset: check that last_seen is after your Test.',
      where: 'Cortex › XQL',
      verifier: 'cortex',
      fields: ({ shared }) => [['Dataset', datasetName(shared.vendor, shared.product) || defaultTarget?.dataset || '<vendor>_<product>_raw']],
      command: ({ shared }) => `dataset = ${datasetName(shared.vendor, shared.product) || defaultTarget?.dataset || '<vendor>_<product>_raw'}\n| filter event_type = "cortexsim_collector_preflight"\n| comp count() as found, max(_insert_time) as last_seen`,
    },
  ], [defaultTarget, refresh])

  const services = targets.flatMap((t) => {
    const v = VERDICT[t.last_check?.verdict]
    const at = serverDate(t.last_check?.checked_at)
    return [
      {
        id: `ep-${t.name}`,
        name: `Collector endpoint · ${t.name}`,
        status: v ? v.status : 'unknown',
        since: v ? `${v.text}${t.last_check?.code && v.status === 'down' ? ` (${t.last_check.code})` : ''}${at ? ` · ${at.toLocaleString()}` : ''}` : 'never checked',
        drill: v?.status === 'up' ? { tab: 'targets' } : { tab: 'setup', step: 'send' },
      },
      {
        id: `ds-${t.name}`,
        name: `Dataset · ${t.dataset || 'not named'}`,
        status: 'unknown',
        word: "Can't check",
        since: 'arrival read needs the HTTP collector backend',
        drill: { tab: 'setup', step: 'arrived' },
      },
    ]
  })

  const lastVerdict = defaultTarget?.last_check?.verdict
  const tick = (id) => !!confirmed[`${KEY}:${id}`]
  const config = [
    {
      id: 'exists',
      label: 'Collector exists in Cortex',
      state: lastVerdict === 'accepted' ? 'ok' : tick('cortex') ? 'confirmed' : 'missing',
      detail: lastVerdict === 'accepted' ? 'it accepted a test record' : undefined,
      drill: { tab: 'setup', step: 'cortex' },
    },
    {
      id: 'json',
      label: 'Log format is JSON',
      // delivery.py files a 5xx (but 503) as collector_error; for the HTTP log
      // collector that is the body not matching its Log Format.
      state: defaultTarget?.last_check?.code === 'collector_error' ? 'missing' : tick('format') ? 'confirmed' : 'cant_check',
      detail: defaultTarget?.last_check?.code === 'collector_error' ? 'the collector answered a server error — almost always the log format' : undefined,
      drill: { tab: 'setup', step: 'format' },
    },
    {
      id: 'matches',
      label: 'Dataset matches the declared detections',
      state: 'cant_check',
      detail: 'needs the HTTP collector backend (data-source declaration)',
      drill: { tab: 'setup', step: 'format' },
    },
    {
      id: 'arrives',
      label: 'Dataset receives the test records',
      state: tick('arrived') ? 'confirmed' : 'cant_check',
      drill: { tab: 'setup', step: 'arrived' },
    },
  ]

  const onTest = useCallback(async () => {
    if (!defaultTarget) return { state: 'failed', issue: 'No collector target', step: 'add' }
    const pf = await preflightCollectorTarget(defaultTarget.name, { writeCanary: false })
    refresh()
    if (pf?.verdict === 'reachable' || pf?.verdict === 'accepted') return { state: 'passed' }
    return { state: 'failed', issue: pf?.code || pf?.verdict || 'Not reachable', step: 'add' }
  }, [defaultTarget, refresh])

  return (
    <ComponentPage
      componentKey={KEY}
      name={meta.name}
      icon={meta.icon}
      status={statuses[KEY]}
      issueStep="send"
      services={services}
      servicesEmpty="No collector target is stored."
      config={config}
      manageTab={{ id: 'targets', label: 'Collector targets', render: () => <CollectorTargetsPanel /> }}
      setup={() => (
        <ConfigureFrame
          confirmPrefix={KEY}
          steps={steps}
          current={params.step || null}
          onSelect={(step) => setParams({ step })}
          ctx={{ env }}
        />
      )}
      onTest={onTest}
      params={params}
      setParams={setParams}
      onNavigate={onNavigate}
      testId="component-http"
    />
  )
}
