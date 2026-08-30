/**
 * onboardingState — the ONLY module that touches onboarding localStorage.
 *
 * Every read fails CLOSED: if storage is unavailable (private window, blocked
 * site data, a browser that throws on access) we report the tour as already
 * seen. Repeatedly showing a walkthrough to someone we are structurally unable
 * to remember is worse than never showing it.
 */
const TOUR_KEY = 'cortexsim.onboarding.tourSeenV1'
const HINT_PREFIX = 'cortexsim.onboarding.hint.'

function read(key) {
  try { return window.localStorage.getItem(key) } catch { return undefined }
}

function write(key, value) {
  try { window.localStorage.setItem(key, value) } catch { /* storage blocked */ }
}

export function tourSeen() {
  const v = read(TOUR_KEY)
  if (v === undefined) return true      // unreadable storage → fail closed
  return v === 'true'
}

export function markTourSeen() { write(TOUR_KEY, 'true') }

export function hintUsed(controlId) {
  const v = read(HINT_PREFIX + controlId)
  if (v === undefined) return true
  return v === 'true'
}

export function markHintUsed(controlId) { write(HINT_PREFIX + controlId, 'true') }

export function resetOnboarding() {
  try {
    const doomed = []
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const k = window.localStorage.key(i)
      if (k === TOUR_KEY || (k && k.startsWith(HINT_PREFIX))) doomed.push(k)
    }
    doomed.forEach((k) => window.localStorage.removeItem(k))
  } catch { /* storage blocked */ }
}
