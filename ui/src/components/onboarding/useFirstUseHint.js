import { useCallback, useState } from 'react'
import { hintUsed, markHintUsed } from './onboardingState.js'

/**
 * useFirstUseHint — a one-time pointer on a consequential control.
 *
 * Cleared when the control is USED, not when the bubble is dismissed:
 * dismissing a hint you did not act on is not evidence you learned it.
 */
export function useFirstUseHint(controlId) {
  const [show, setShow] = useState(() => !hintUsed(controlId))

  const onUse = useCallback(() => {
    markHintUsed(controlId)
    setShow(false)
  }, [controlId])

  return { show, onUse }
}
