/**
 * WhyThisMatters — the block a deep-dive sheet opens with (design v33 C10):
 * Thesis · Why we simulate it · What proves it · What it needs, in a 2 × 2
 * grid (one column on a phone). The parts come from `sheetThesis.js`, which
 * reads them off the document; a part with nothing written says so plainly.
 *
 * Props:
 *   parts         the four parts from scenarioThesis / ttpThesis / detectionThesis
 *   headingLevel  2–6, so the block sits in the sheet's own outline
 *   testId        data-testid of the section
 */
import React from 'react'
import Icon from './kit/Icon.jsx'
import '../../styles/destinations/sheet-thesis.css'

void React

export default function WhyThisMatters({ parts, headingLevel = 3, testId = 'why-this-matters' }) {
  const Heading = `h${Math.min(6, Math.max(2, headingLevel))}`
  const id = React.useId()
  return (
    <section className="why-matters" aria-labelledby={id} data-testid={testId}>
      <Heading id={id} className="why-matters__title">Why this matters</Heading>
      <ul className="why-matters__grid">
        {parts.map((p) => (
          <li
            key={p.key}
            className={`why-matters__part why-matters__part--${p.key}${p.written ? '' : ' why-matters__part--unwritten'}`}
            data-part={p.key}
            data-written={p.written ? 'true' : 'false'}
          >
            <span className="why-matters__glyph" aria-hidden="true"><Icon name={p.icon} size={12} /></span>
            <div className="why-matters__body">
              <span className="why-matters__label">{p.label}</span>
              {p.lines.map((line, i) => (
                <p key={i} className="why-matters__line">{line}</p>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}
