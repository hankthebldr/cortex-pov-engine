/**
 * useToast — say that an action finished, from anywhere in the console
 * (design v26 §4.1: every action that completes in place confirms through the
 * Toast).
 *
 * AppConsole owns the one Toast and provides its `surfaceToast` here. Outside
 * the console (an isolated component test) the hook returns a no-op, so a
 * surface never has to check whether anyone is listening.
 */
import { createContext, useContext } from 'react'

const noop = () => {}

export const ToastContext = createContext(noop)

/** @returns {(message: string, type?: 'success'|'info'|'warn'|'error', ms?: number) => void} */
export default function useToast() {
  return useContext(ToastContext) || noop
}
