/**
 * ChainTray — the "Your chain" bar (design v26 §4.4).
 *
 * Shown above Scenarios and the Catalog while something has been added with
 * "Add to chain". It names what is waiting, clears it, or carries it into
 * Build a chain (Customize), where each item becomes a step. Renders nothing
 * while the tray is empty.
 */
import React from 'react'
import { useChainTray } from '../../context/ChainTrayContext.jsx'
import Button from './kit/Button.jsx'

void React

export default function ChainTray({ onNavigate = () => {} }) {
  const { items, clear } = useChainTray()
  if (!items.length) return null
  return (
    <section className="chain-tray" role="region" aria-label="Your chain" data-testid="chain-tray">
      <span className="chain-tray__title">Your chain</span>
      <span className="chain-tray__items" title={items.map((i) => i.label).join(' · ')}>
        {items.length} added — {items.map((i) => i.label).join(' · ')}
      </span>
      <button type="button" className="chain-tray__clear" onClick={clear}>Clear</button>
      <Button variant="primary" onClick={() => onNavigate('composer', { tray: '1' })}>Customize</Button>
    </section>
  )
}
