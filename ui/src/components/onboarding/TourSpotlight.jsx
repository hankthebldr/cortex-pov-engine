import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'

/**
 * TourSpotlight — dim layer with a cutout over the anchored element, plus a
 * bubble beside it.
 *
 * The cutout is drawn with a very large box-shadow spread rather than an SVG
 * mask: it needs no extra element, scales to any viewport, and degrades to a
 * plain dim layer if the rect is unavailable. When a cutout rect IS resolved,
 * the box-shadow spread is the only dimming — the full-viewport `.tour__dim`
 * layer is deliberately not rendered, because it would sit on top of the
 * "hole" and swallow both the visual cutout and clicks on the spotlit
 * element (`pointer-events: none` on the cutout div only stops the cutout
 * div itself from capturing the click; it does nothing about a dim layer
 * underneath it).
 *
 * Focus is trapped inside the bubble — Tab/Shift+Tab wrap within the
 * ordered set [bubble, Skip, Back?, Next/Done] — and restored to whatever
 * had focus before the tour opened once the bubble unmounts or the stop
 * changes.
 */
export default function TourSpotlight({ stop, index, total, onNext, onPrev, onExit }) {
  const [rect, setRect] = useState(null)
  const bubbleRef = useRef(null)

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

  useEffect(() => {
    if (!stop) return undefined
    const previouslyFocused = document.activeElement
    if (bubbleRef.current) bubbleRef.current.focus()
    return () => {
      if (
        previouslyFocused &&
        typeof previouslyFocused.focus === 'function' &&
        document.contains(previouslyFocused)
      ) {
        previouslyFocused.focus()
      }
    }
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
