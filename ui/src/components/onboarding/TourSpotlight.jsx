import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'

/**
 * TourSpotlight — dim layer with a cutout over the anchored element, plus a
 * bubble beside it.
 *
 * The "hole" is a `pointer-events: none` bordered box (no box-shadow — a
 * shadow is paint-only and does not participate in hit-testing) surrounded
 * by four `position: fixed` dim "shutters" (top/bottom/left/right), each
 * `pointer-events: auto`. That combination is what makes a click on the
 * spotlit element land while every other pixel of the dimmed background
 * still swallows the click. Degrades to a single full-viewport `.tour__dim`
 * layer when the anchor can't be measured (nothing to cut a hole around).
 *
 * Focus is trapped inside the bubble — Tab/Shift+Tab wrap within the
 * ordered set [bubble, Skip, Back?, Next/Done] — and restored, once, to
 * whatever had focus before the tour opened, in an unmount cleanup. That
 * restore is deliberately mount-scoped (captured once via a ref, restored
 * once on unmount) rather than re-keyed on `stop`: tying it to `stop`
 * bounces focus out to the external trigger and back on every Next/Back,
 * which is spurious churn a screen-reader user would hear on every step.
 * Focusing the bubble itself DOES need to re-run per stop — that stays a
 * separate effect.
 */
export default function TourSpotlight({ stop, index, total, onNext, onPrev, onExit }) {
  const [rect, setRect] = useState(null)
  const bubbleRef = useRef(null)
  const previouslyFocusedRef = useRef(null)

  useLayoutEffect(() => {
    if (!stop) { setRect(null); return undefined }
    const measure = () => {
      const el = document.querySelector(`[data-tour-id="${stop.anchor}"]`)
      setRect(el ? el.getBoundingClientRect() : null)
    }
    measure()
    window.addEventListener('resize', measure)
    window.addEventListener('scroll', measure, true)
    return () => {
      window.removeEventListener('resize', measure)
      window.removeEventListener('scroll', measure, true)
    }
  }, [stop])

  useEffect(() => {
    if (!stop) return undefined
    const getFocusables = () => {
      const root = bubbleRef.current
      if (!root) return []
      return [root, ...Array.from(root.querySelectorAll('button'))]
    }
    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onExit()
      } else if (e.key === 'Enter') {
        e.preventDefault()
        onNext()
      } else if (e.key === 'Tab') {
        const items = getFocusables()
        if (items.length === 0) return
        e.preventDefault()
        const activeIndex = items.indexOf(document.activeElement)
        let nextIndex
        if (e.shiftKey) {
          nextIndex = activeIndex <= 0 ? items.length - 1 : activeIndex - 1
        } else {
          nextIndex = activeIndex === -1 || activeIndex === items.length - 1 ? 0 : activeIndex + 1
        }
        items[nextIndex].focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [stop, onExit, onNext])

  // Mount-scoped: capture the pre-tour focus once, restore it once, on
  // unmount only. NOT keyed on `stop` — see the header comment.
  useEffect(() => {
    previouslyFocusedRef.current = document.activeElement
    return () => {
      const el = previouslyFocusedRef.current
      if (el && typeof el.focus === 'function' && document.contains(el)) {
        el.focus()
      }
    }
  }, [])

  // Per-stop: move focus into the bubble on every stop change.
  useEffect(() => {
    if (stop && bubbleRef.current) bubbleRef.current.focus()
  }, [stop])

  if (!stop) return null

  const isLast = index >= total - 1
  const cut = rect
    ? { top: rect.top - 6, left: rect.left - 6, width: rect.width + 12, height: rect.height + 12 }
    : null

  return (
    <div className="tour" data-testid="tour-spotlight">
      {!cut && <div className="tour__dim" aria-hidden="true" />}
      {cut && (
        <>
          <div
            className="tour__shutter"
            aria-hidden="true"
            style={{ left: 0, top: 0, width: '100vw', height: cut.top }}
          />
          <div
            className="tour__shutter"
            aria-hidden="true"
            style={{ left: 0, top: cut.top + cut.height, width: '100vw', bottom: 0 }}
          />
          <div
            className="tour__shutter"
            aria-hidden="true"
            style={{ left: 0, top: cut.top, width: cut.left, height: cut.height }}
          />
          <div
            className="tour__shutter"
            aria-hidden="true"
            style={{ left: cut.left + cut.width, top: cut.top, right: 0, height: cut.height }}
          />
        </>
      )}
      {cut && (
        <div
          className="tour__cutout"
          aria-hidden="true"
          style={{ top: cut.top, left: cut.left, width: cut.width, height: cut.height }}
        />
      )}
      <div
        className="tour__bubble"
        role="dialog"
        aria-modal="true"
        aria-label={stop.title}
        tabIndex={-1}
        ref={bubbleRef}
        style={cut ? { top: cut.top + cut.height + 12, left: cut.left } : undefined}
      >
        <h2 className="tour__title">{stop.title}</h2>
        <p className="tour__body">{stop.body}</p>
        <div className="tour__foot">
          <button type="button" className="btn btn--xs" onClick={onExit}>Skip</button>
          <span className="tour__progress" aria-live="polite">{index + 1} of {total}</span>
          {index > 0 && <button type="button" className="btn btn--xs" onClick={onPrev}>Back</button>}
          <button type="button" className="btn btn--primary btn--xs" onClick={isLast ? onExit : onNext}>
            {isLast ? 'Done' : 'Next'}
          </button>
        </div>
      </div>
    </div>
  )
}
