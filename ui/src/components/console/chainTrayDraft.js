/**
 * chainTrayDraft.js — turn chain-tray items into Composer steps. Pure.
 *
 * Each item becomes ONE real step the existing draft operations already know
 * how to build, so nothing here invents a step shape:
 *
 *   ttp      a step named for the card, bound to its detection
 *            (`bindTtpDetection` — the path that satisfies the launch gate)
 *   stream   a step on the EAL channel running that emitter (`setStepChannel`
 *            + `setStepEal`); its lane is read from the plugin like any other
 *   package  a step whose command is `{adapter:<id>}`, plus the adapter in
 *            `externalTools` — the launch consent gate and the payload shelf
 *            read `adapter_ref`s from there, never from the command. The
 *            placeholder resolves on PULL launches (the orchestrator); a push
 *            bundle does not expand it.
 *
 * New steps extend the chain the way `+ Add step` does: each is parented to
 * the step before it (process_lineage) once the chain has a spine to extend,
 * so a tray of three becomes one spine, not three roots the loader would
 * reject.
 */
import {
  appendStep, bindTtpDetection, blankStep, editStep, setCausalityParent, setStepChannel, setStepEal,
} from './composerDraft.js'

/**
 * Should the step just appended (`newId`) hang from the one before it? The
 * same rule `+ Add step` follows: yes when the chain before it was one step or
 * already declares a spine; no for a legacy star of several roots, where one
 * declared parent would make every other root off-chain.
 */
function extendsSpine(steps, newId) {
  const before = steps.filter((s) => s.id !== newId)
  if (before.length === 1) return true
  const ids = new Set(before.map((s) => s.id))
  return before.some((s) => s.causalityParent && ids.has(s.causalityParent))
}

/**
 * @param {Array}    steps          current draft steps
 * @param {Array}    items          tray items `{kind,id,label,name?,technique?,plane?}`
 * @param {Function} mintId         `(steps) => newStepId` — the Composer's own id minting
 * @param {Array}    [externalTools] the draft's current `externalTools`
 * @returns {{steps:Array, externalTools:Array, added:number}}
 */
export function appendTrayItems(steps, items, mintId, externalTools = []) {
  let out = Array.isArray(steps) ? steps : []
  const tools = Array.isArray(externalTools) ? externalTools.slice() : []
  let added = 0
  for (const item of Array.isArray(items) ? items : []) {
    if (!item || !item.id) continue
    const prev = out.length ? out[out.length - 1].id : null
    const id = mintId(out)
    const name = item.name || item.label || item.id
    if (item.kind === 'ttp') {
      out = appendStep(out, blankStep(id, { name, technique: item.technique || null }))
      out = bindTtpDetection(out, id, { id: item.id, name, plane: item.plane || null })
    } else if (item.kind === 'stream') {
      out = appendStep(out, blankStep(id, { name }))
      out = setStepChannel(out, id, 'eal')
      out = setStepEal(out, id, { plugin: item.id })
    } else if (item.kind === 'package') {
      out = appendStep(out, blankStep(id, { name }))
      out = editStep(out, id, { command: `{adapter:${item.id}}` })
      // `name` and `type` are required by the loader's ExternalToolSchema. For
      // an adapter-backed tool `type` drives nothing: the adapter installs it
      // and the push generator skips its dependency check.
      if (!tools.some((t) => t && t.adapter_ref === item.id)) {
        tools.push({ name: item.name || item.id, type: 'binary', adapter_ref: item.id })
      }
    } else {
      continue
    }
    if (prev && extendsSpine(out, id)) {
      out = setCausalityParent(out, id, prev, 'process_lineage', { skipOrderCheck: true })
    }
    added += 1
  }
  return { steps: out, externalTools: tools, added }
}
