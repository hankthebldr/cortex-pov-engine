import React, { useState, useEffect, useMemo, useCallback } from 'react'
import ScenarioGrid from './ScenarioGrid.jsx'
import ScenarioInspector from './ScenarioInspector.jsx'
import { getScenarios, getScenario } from '../../api/client.js'

/**
 * OperationsView — the default tab in AppConsole.
 *
 * Composes:
 *   - a head with title + plane/filter context
 *   - a scenario card grid
 *   - the right-side ScenarioInspector drawer (pinned launch CTA at top)
 *
 * Selecting a card hydrates the full scenario detail (so the matrix has
 * steps + expected detections), then opens the drawer.
 *
 * Props:
 *   selectedPlane    — uppercase plane code or null (filter)
 *   onRunComplete    — (run) => void  forwarded from parent
 *   onError          — (msg) => void  forwarded from parent
 */
export default function OperationsView({
  selectedPlane = null,
  onRunComplete = () => {},
  onError = () => {},
}) {
  const [scenarios, setScenarios]   = useState([])
  const [loading, setLoading]       = useState(true)
  const [selected, setSelected]     = useState(null)        // detailed scenario
  const [drawerOpen, setDrawerOpen] = useState(false)

  // ── Fetch scenario list ──────────────────────────────────────────────
  useEffect(() => {
    setLoading(true)
    const params = selectedPlane ? { plane: selectedPlane } : {}
    getScenarios(params)
      .then((data) => {
        const list = Array.isArray(data) ? data : (data && data.scenarios) || []
        setScenarios(list)
      })
      .catch((err) => {
        onError(err.message || 'Failed to load scenarios')
        setScenarios([])
      })
      .finally(() => setLoading(false))
  }, [selectedPlane]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Handle card selection — hydrate full detail ──────────────────────
  const handleSelect = useCallback(async (summary) => {
    const id = summary.scenario_id || summary.id
    setSelected(summary)         // optimistic — show whatever we have
    setDrawerOpen(true)
    try {
      const detail = await getScenario(id)
      setSelected(detail || summary)
    } catch (err) {
      // Keep the summary we already have; drawer will note "no steps loaded".
      onError(err.message || `Failed to load ${id}`)
    }
  }, [onError])

  const handleClose = useCallback(() => {
    setDrawerOpen(false)
  }, [])

  const selectedId = selected?.scenario_id || selected?.id || null

  const headMeta = useMemo(() => {
    return {
      planeLabel: selectedPlane || 'all planes',
      count: scenarios.length,
      lastUpdated: new Date().toISOString().substring(11, 19) + 'Z',
    }
  }, [selectedPlane, scenarios])

  return (
    <div className="operations grid-bg">
      <div className="view-head">
        <div>
          <h1>Operations</h1>
          <div className="view-head__meta">
            Plane: <strong>{headMeta.planeLabel}</strong>
            {' · '}<span className="mono">{headMeta.count} scenarios</span>
            {' · '}last updated <span className="mono">{headMeta.lastUpdated}</span>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {/* These are placeholders for migration step 5 (filtering / new scenario UX) */}
          <button className="btn" disabled title="Filter palette — pending step 5">
            <span>Filter</span>
            <span className="kbd">F</span>
          </button>
          <button className="btn" disabled title="New scenario authoring — backlog">
            <span>New scenario</span>
            <span className="kbd">N</span>
          </button>
        </div>
      </div>

      {loading ? (
        <div style={{
          padding: 48,
          textAlign: 'center',
          color: 'var(--c-text-muted)',
          fontFamily: 'var(--font-mono)',
          fontSize: 11,
          letterSpacing: '0.04em',
        }}>
          loading scenarios…
        </div>
      ) : (
        <ScenarioGrid
          scenarios={scenarios}
          selectedScenarioId={selectedId}
          onSelectScenario={handleSelect}
        />
      )}

      <ScenarioInspector
        scenario={selected}
        open={drawerOpen}
        onClose={handleClose}
        onRunComplete={(run) => { onRunComplete(run); setDrawerOpen(false) }}
        onError={onError}
      />
    </div>
  )
}
