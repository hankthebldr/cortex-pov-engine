import React, { useState, useEffect } from 'react'
import { getScenarios } from '../api/client.js'

// ─── Plane Definitions ────────────────────────────────────────────────────────

const PLANES = [
  {
    id: 'EDR',
    label: 'EDR',
    description: 'Endpoint Detection & Response',
    icon: '🖥',
  },
  {
    id: 'CDR',
    label: 'CDR',
    description: 'Container Detection & Response',
    icon: '📦',
  },
  {
    id: 'NDR',
    label: 'NDR',
    description: 'Network Detection & Response',
    icon: '🌐',
  },
  {
    id: 'ITDR',
    label: 'ITDR',
    description: 'Identity Threat Detection & Response',
    icon: '🔐',
  },
  {
    id: 'CLOUD_APP',
    label: 'Cloud App',
    description: 'Cloud Application Security',
    icon: '☁',
  },
  {
    id: 'ANALYTICS',
    label: 'Analytics',
    description: 'XSIAM Correlation Engine',
    icon: '📊',
  },
]

// ─── Component ────────────────────────────────────────────────────────────────

export default function PlaneSelector({ selectedPlane, onSelectPlane }) {
  const [counts, setCounts] = useState({})
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    getScenarios()
      .then(scenarios => {
        const c = {}
        if (Array.isArray(scenarios)) {
          scenarios.forEach(s => {
            const plane = (s.plane || '').toUpperCase()
            c[plane] = (c[plane] || 0) + 1
          })
        }
        setCounts(c)
      })
      .catch(() => setCounts({}))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div>
      <p className="section-label">Detection Planes</p>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
        {PLANES.map(plane => {
          const isActive = selectedPlane === plane.id
          const count = counts[plane.id] || 0

          return (
            <button
              key={plane.id}
              onClick={() => onSelectPlane(plane.id)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '10px',
                padding: '10px 12px',
                borderRadius: 'var(--radius-md)',
                border: isActive
                  ? '1.5px solid var(--cortex-teal)'
                  : '1.5px solid transparent',
                background: isActive
                  ? 'rgba(0, 192, 232, 0.08)'
                  : 'transparent',
                color: isActive ? 'var(--cortex-navy)' : '#3a4f62',
                cursor: 'pointer',
                textAlign: 'left',
                width: '100%',
                transition: 'all var(--transition-fast)',
              }}
              aria-pressed={isActive}
              title={plane.description}
            >
              {/* Icon */}
              <span style={{ fontSize: '18px', lineHeight: 1, flexShrink: 0 }}>
                {plane.icon}
              </span>

              {/* Text */}
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{
                  display: 'block',
                  fontSize: '13px',
                  fontWeight: isActive ? 600 : 500,
                  lineHeight: 1.2,
                }}>
                  {plane.label}
                </span>
                <span style={{
                  display: 'block',
                  fontSize: '10px',
                  color: 'var(--cortex-steel)',
                  lineHeight: 1.3,
                  marginTop: '1px',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}>
                  {plane.description}
                </span>
              </span>

              {/* Count badge */}
              <span style={{
                fontSize: '11px',
                fontWeight: 600,
                minWidth: '20px',
                height: '20px',
                borderRadius: '10px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                background: isActive
                  ? 'rgba(0,192,232,0.18)'
                  : 'var(--cortex-light-bg)',
                color: isActive ? 'var(--cortex-teal)' : 'var(--cortex-steel)',
                border: '1px solid var(--cortex-border)',
                flexShrink: 0,
              }}>
                {loading ? '·' : count}
              </span>
            </button>
          )
        })}
      </div>

      {/* "All" reset */}
      {selectedPlane && (
        <button
          className="btn btn-secondary btn-sm btn-full"
          onClick={() => onSelectPlane(selectedPlane)}  // clicking active plane deselects
          style={{ marginTop: '12px' }}
        >
          &#x2715; Clear filter
        </button>
      )}
    </div>
  )
}
