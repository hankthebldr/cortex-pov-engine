/**
 * ReadBackButton — Read back on a run that already has a verdict, behind a
 * confirm (decision D5).
 *
 * Read back is the one tenant read that can change a run's verdict, so where a
 * verdict is already on screen it asks first, and the copy says what the
 * neighbouring refresh of tenant evidence does not do. It goes through the
 * console's one Read back path (useReadBack: in-flight guard, toast, runs
 * refresh) and never posts on its own. The unread phase's Read back is the
 * run's primary action and stays one click (design v26 §4.6); this is for the
 * places beside an existing verdict.
 */
import React, { useState } from 'react'
import ConfirmDialog from './ConfirmDialog.jsx'
import useReadBack from './useReadBack.jsx'
import { Button } from './kit/index.js'

void React

export const READ_BACK_CONFIRM_TITLE = 'Read back this run?'
export const READ_BACK_CONFIRM_BODY =
  "Read back reads the tenant and may change this run's verdict, marking detections "
  + 'detected or missed. Refreshing tenant evidence never does.'

/**
 * @param {{ runId: string, onDone?: (outcome: object|null) => void, variant?: 'primary'|'secondary' }} props
 *   onDone gets the read's outcome (`{ok, message, summary?}`), or null when
 *   another read of the same run was already in flight.
 */
export default function ReadBackButton({ runId, onDone = () => {}, variant = 'secondary' }) {
  const { reading, readBack } = useReadBack()
  const [open, setOpen] = useState(false)
  if (!runId) return null
  const busy = reading.has(runId)

  const confirm = async () => {
    setOpen(false)
    const outcome = await readBack(runId)
    onDone(outcome)
  }

  return (
    <>
      <Button
        variant={variant}
        disabled={busy}
        aria-busy={busy || undefined}
        onClick={() => setOpen(true)}
      >
        {busy ? 'Reading…' : 'Read back'}
      </Button>
      <ConfirmDialog
        open={open}
        onClose={() => setOpen(false)}
        onConfirm={confirm}
        title={READ_BACK_CONFIRM_TITLE}
        body={READ_BACK_CONFIRM_BODY}
        confirmLabel="Read back"
        confirmVariant="primary"
        cancelLabel="Cancel"
        initialFocus="cancel"
      />
    </>
  )
}
