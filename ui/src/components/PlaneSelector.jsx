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

      <div className="plane-list">
        {PLANES.map(plane => {
          const isActive = selectedPlane === plane.id
          const count = counts[plane.id] || 0

          return (
            <button
              key={plane.id}
              onClick={() => onSelectPlane(plane.id)}
              data-testid={`plane-button-${plane.id}`}
              className={'plane-btn' + (isActive ? ' plane-btn--active' : '')}
              aria-pressed={isActive}
              title={plane.description}
            >
              {/* Icon */}
              <span className="plane-btn__icon">
                {plane.icon}
              </span>

              {/* Text */}
              <span className="plane-btn__text">
                <span className={'plane-btn__label' + (isActive ? ' plane-btn__label--active' : '')}>
                  {plane.label}
                </span>
                <span className="plane-btn__desc">
                  {plane.description}
                </span>
              </span>

              {/* Count badge */}
              <span className={'plane-btn__count' + (isActive ? ' plane-btn__count--active' : '')}>
                {loading ? '·' : count}
              </span>
            </button>
          )
        })}
      </div>

      {/* "All" reset */}
      {selectedPlane && (
        <button
          className="btn btn-secondary btn-sm btn-full plane-clear-btn"
          onClick={() => onSelectPlane(selectedPlane)}  // clicking active plane deselects
        >
          &#x2715; Clear filter
        </button>
      )}
    </div>
  )
}
