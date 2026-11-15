/**
 * ChainTrayContext — what a DC has picked to build a chain from (design v26 §4.4).
 *
 * "Add to chain" on any catalog item (a TTP card, a package, a data stream),
 * anywhere in the console, drops the item here and confirms by toast. The tray
 * bar shows what is waiting; its Customize carries the items into Build a
 * chain, which turns each into a real step and empties the tray.
 *
 * An item is `{ kind: 'ttp' | 'package' | 'stream', id, label, ...facts }`.
 * The same kind + id is held once: adding it again says so instead.
 *
 * `notify(message, type)` is the shell's toast (AppConsole's `surfaceToast`).
 * Without a provider the tray is empty and `add` does nothing, so a view
 * rendered on its own still renders.
 */
import React, { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react'

void React

const KINDS = new Set(['ttp', 'package', 'stream'])

const NONE = Object.freeze({
  items: [],
  add: () => false,
  remove: () => {},
  clear: () => {},
  take: () => [],
})

const ChainTrayContext = createContext(NONE)

export function ChainTrayProvider({ notify = () => {}, children }) {
  const [items, setItems] = useState([])
  // The latest list, so two adds in one tick both see each other.
  const ref = useRef(items)

  const commit = useCallback((next) => {
    ref.current = next
    setItems(next)
  }, [])

  const add = useCallback((item, { quiet = false } = {}) => {
    if (!item || !KINDS.has(item.kind) || !item.id) return false
    const label = item.label || item.id
    if (ref.current.some((i) => i.kind === item.kind && i.id === item.id)) {
      if (!quiet) notify(`${label} is already in your chain`, 'info')
      return false
    }
    commit([...ref.current, { ...item, label }])
    if (!quiet) notify(`Added ${label} to your chain`, 'success')
    return true
  }, [commit, notify])

  const remove = useCallback((kind, id) => {
    commit(ref.current.filter((i) => !(i.kind === kind && i.id === id)))
  }, [commit])

  const clear = useCallback(() => commit([]), [commit])

  /** Hand every item over and empty the tray (Build a chain consumes it). */
  const take = useCallback(() => {
    const out = ref.current
    if (out.length) commit([])
    return out
  }, [commit])

  const value = useMemo(() => ({ items, add, remove, clear, take }), [items, add, remove, clear, take])
  return <ChainTrayContext.Provider value={value}>{children}</ChainTrayContext.Provider>
}

export function useChainTray() {
  return useContext(ChainTrayContext)
}

/** The tray item for a TTP card (list or detail shape). */
export function ttpTrayItem(ttp) {
  const id = ttp?.id || ttp?.ttp_id
  // The list carries `name` / `technique_ids`; the detail nests them in `identity`.
  const name = ttp?.name || ttp?.identity?.name || null
  const techniques = ttp?.technique_ids || ttp?.identity?.technique_ids || []
  return {
    kind: 'ttp',
    id,
    label: name ? `${id} · ${name}` : id,
    name: name || id,
    technique: techniques[0] || ttp?.mitre_technique || null,
    plane: ttp?.plane || null,
  }
}

/** The tray item for a tool adapter (a package). */
export function packageTrayItem(adapter) {
  const id = adapter?.adapter_id || adapter?.id
  return { kind: 'package', id, label: adapter?.name ? `${id} · ${adapter.name}` : id, name: adapter?.name || id }
}

/** The tray item for a data stream: its emitter, an EAL plugin. */
export function streamTrayItem(emitter) {
  const id = emitter?.name
  return { kind: 'stream', id, label: id, name: id }
}
