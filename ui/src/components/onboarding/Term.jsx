import React, { useId, useState } from 'react'
import { lookup } from './glossary.js'

/**
 * Term — hover/focus tooltip for a CortexSim vocabulary word.
 *
 * An unknown key renders plain text with NO tooltip. An empty tooltip and an
 * absent tooltip must not look the same: a dangling key is caught by the
 * glossary guard test, not papered over at runtime.
 */
export default function Term({ k, children }) {
  const entry = lookup(k)
  const [open, setOpen] = useState(false)
  const id = useId()

  if (!entry) return <>{children}</>

  return (
    <span className="term-wrap">
      <span
        className="term"
        tabIndex={0}
        aria-describedby={open ? id : undefined}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
      >
        {children}
      </span>
      {open && (
        <span role="tooltip" id={id} className="term__tip">
          {entry.definition}
        </span>
      )}
    </span>
  )
}
