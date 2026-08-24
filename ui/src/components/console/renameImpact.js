/**
 * renameImpact.js — "what does renaming this payload actually cost?"
 *
 * Henry's standing point, made computable: XDR should catch a privilege-
 * escalation sweep as malicious BEHAVIOUR — a process reading /etc/shadow,
 * walking /proc, sweeping SUID binaries — not as a tool it recognises by
 * filename. Two BIOCs in this corpus literally read
 *
 *     | filter action_process_image_command_line contains "linpeas"
 *
 * so staging the artifact as `/tmp/.cache/sysinfo.sh` turns the run into a
 * NEGATIVE CONTROL: those must go dark, and the behavioural ones must not.
 *
 * WHAT THIS FUNCTION IS ALLOWED TO CLAIM
 * --------------------------------------
 * SimCore exposes no endpoint that partitions detections by filename-keying, so
 * this is computed in the console from two sources it can actually see:
 *
 *   1. the scenario's own `expected_detections[]` — `detection_id` / `description`
 *   2. the TTP card(s) named by `ttp_ref` — the EXECUTABLE text of each
 *      detection (`logic`, `query`, `value`). Never `description`: prose that
 *      mentions "LinPEAS" does not make a detection name-keyed, and counting it
 *      would inflate the suppressed column with detections that will fire.
 *
 * Anything it cannot see lands in `unknown[]` and is rendered as UNKNOWN. It is
 * NEVER folded into "still proves" — telling a DC a detection is behavioural
 * when we did not read its query is precisely the kind of confident-and-wrong
 * claim that turns a designed control into an apparent coverage gap in front of
 * a customer.
 */

/** lower-case, non-alphanumerics collapsed to '-'. Mirrors how the corpus's
 *  `detection_id` slugs are derived from a card detection's `name`. */
export function slugify(text) {
  return String(text || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

/**
 * The tokens a filename-keyed detection would contain.
 * `linpeas.sh` → ['linpeas.sh', 'linpeas'].
 */
export function filenameTokens(filename) {
  const base = String(filename || '').split('/').pop().toLowerCase()
  if (!base) return []
  const stem = base.replace(/\.[^.]+$/, '')
  return stem && stem !== base ? [base, stem] : [base]
}

/** Every detection object in a card, flattened with its family. */
function cardDetections(card) {
  const out = []
  const det = card?.detections || {}
  const families = [
    ['bioc', det.biocs],
    ['xql', det.xql_queries],
    ['correlation', det.correlation_rules],
    ['ioc', det.iocs],
    ['abioc', det.abiocs],
    ['analytics', det.analytics_modules],
  ]
  for (const [family, list] of families) {
    if (!Array.isArray(list)) continue
    for (const d of list) {
      if (!d || typeof d !== 'object') continue
      out.push({
        family,
        name: d.name || d.rule_id || d.value || '',
        // EXECUTABLE text only — see the header. `description` is excluded.
        text: [d.logic, d.query, d.value].filter((x) => typeof x === 'string').join('\n'),
      })
    }
  }
  return out
}

/** The first line of `text` containing one of `tokens`, trimmed for display. */
function matchingLine(text, tokens) {
  for (const line of String(text || '').split('\n')) {
    const lower = line.toLowerCase()
    if (tokens.some((t) => lower.includes(t))) return line.trim()
  }
  return null
}

/**
 * Partition a scenario's expected detections against a payload filename.
 *
 * @param {object}   scenario  scenario detail (steps[].expected_detections[])
 * @param {object}   cards     { [ttp_ref]: cardJson | null }  null = fetch failed
 * @param {string}   filename  the payload's OWN name, e.g. "linpeas.sh"
 * @returns {{nameKeyed:Array, notNameKeyed:Array, unknown:Array, tokens:Array,
 *           cardsScanned:number, cardsMissing:Array}}
 */
export function partitionByFilename({ scenario, cards = {}, filename }) {
  const tokens = filenameTokens(filename)
  const nameKeyed = []
  const notNameKeyed = []
  const unknown = []
  const cardsMissing = new Set()
  const seen = new Set()

  if (!tokens.length) {
    return { nameKeyed, notNameKeyed, unknown, tokens, cardsScanned: 0, cardsMissing: [] }
  }

  // Index every fetched card's detections once.
  const indexed = {}
  let cardsScanned = 0
  for (const [ref, card] of Object.entries(cards)) {
    if (!card) continue
    indexed[ref] = cardDetections(card)
    cardsScanned += 1
  }

  for (const step of scenario?.steps || []) {
    for (const det of step?.expected_detections || []) {
      const id = det?.detection_id
      if (!id || seen.has(id)) continue
      seen.add(id)

      const row = {
        detection_id: id,
        type: det.type || det.detection_type || null,
        plane: det.plane || null,
        description: det.description || '',
        ttp_ref: det.ttp_ref || null,
        step_id: step.id || null,
      }

      // (1) The identifier itself names the tool. Certain, no card needed.
      // Only the id — NOT the description. Prose about a detection is the
      // scenario author's summary, not the predicate that fires; matching on it
      // would move detections that will fire into the suppressed column and
      // make a designed control read as a coverage gap.
      const idHay = String(id).toLowerCase()
      if (tokens.some((t) => idHay.includes(t))) {
        nameKeyed.push({ ...row, evidence: 'identifier', snippet: null })
        continue
      }

      // (2) The card's executable text names the tool.
      const ref = row.ttp_ref
      const pool = ref ? indexed[ref] : null
      if (ref && !pool) {
        cardsMissing.add(ref)
        unknown.push({ ...row, reason: `${ref} could not be read, so its query text was not scanned` })
        continue
      }
      if (!pool) {
        unknown.push({ ...row, reason: 'this detection names no ttp_ref, so no query text could be scanned' })
        continue
      }

      // Join slug → card detection. The corpus derives `detection_id` as
      // `<family>-<slugify(name)>`, so an endsWith on the slug is the join.
      const joined = pool.filter((d) => {
        const slug = slugify(d.name)
        return slug && (id.endsWith(slug) || slugify(id).includes(slug))
      })

      if (!joined.length) {
        // The card loaded but nothing in it maps to this id. We did not read
        // the query that backs this detection, so we cannot call it behavioural.
        unknown.push({ ...row, reason: `no detection in ${ref} maps to this id, so its query text was not scanned` })
        continue
      }

      const hit = joined.find((d) => matchingLine(d.text, tokens))
      if (hit) {
        nameKeyed.push({ ...row, evidence: 'query', snippet: matchingLine(hit.text, tokens) })
      } else {
        notNameKeyed.push({ ...row, evidence: 'query-scanned' })
      }
    }
  }

  return {
    nameKeyed,
    notNameKeyed,
    unknown,
    tokens,
    cardsScanned,
    cardsMissing: Array.from(cardsMissing),
  }
}

/** Every distinct ttp_ref a scenario's expected detections name. */
export function ttpRefsOf(scenario) {
  const refs = new Set()
  for (const step of scenario?.steps || []) {
    for (const det of step?.expected_detections || []) {
      if (det?.ttp_ref) refs.add(det.ttp_ref)
    }
  }
  if (scenario?.ttp_ref) refs.add(scenario.ttp_ref)
  return Array.from(refs)
}

/**
 * Destination-path validation, client-side, mirroring the three server gates
 * (TA-09 on the pack, S-19 on the scenario, PAYLOAD_DEST_REFUSED on a compose
 * request). A destination is a WRITE ON A HOST THE DC DOES NOT OWN, and a
 * beacon frequently runs as root — so the console refuses early and says why,
 * rather than letting the operator discover it as a 400 mid-demo.
 *
 * The allowlist is `adapter_loader._ALLOWED_STAGE_ROOTS`, copied verbatim.
 */
export const ALLOWED_STAGE_ROOTS = ['/tmp/', '/var/tmp/', '/dev/shm/', '/opt/cortexsim/']

/** Names that would make the run unreadable if a payload shadowed them. */
const SHADOWING_BASENAMES = new Set(['bash', 'sh', 'curl', 'wget', 'python3', 'python', 'sudo', 'systemd', 'ls', 'cat'])

const BASENAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/

export function validateStagePath(path) {
  const p = String(path || '')
  if (!p.trim()) return { ok: false, error: 'A destination path is required.' }
  if (!p.startsWith('/')) return { ok: false, error: 'Path must be absolute — start with /' }
  if (/[;&|$`\n\r<>*?()]/.test(p)) {
    return { ok: false, error: 'Shell metacharacters are not allowed in a path.' }
  }
  if (p.split('/').includes('..')) return { ok: false, error: "'..' segments are not allowed." }
  if (p.endsWith('/')) return { ok: false, error: 'Path must name a file, not a directory.' }
  if (!ALLOWED_STAGE_ROOTS.some((root) => p.startsWith(root))) {
    return {
      ok: false,
      error: `Only ${ALLOWED_STAGE_ROOTS.join(', ')} are allowed. CortexSim stages simulation `
        + 'artifacts; it does not install into system paths on a host it does not own.',
    }
  }
  const base = p.split('/').pop()
  if (!BASENAME_RE.test(base)) {
    return { ok: false, error: `'${base}' is not a valid filename (${BASENAME_RE.source}).` }
  }
  if (SHADOWING_BASENAMES.has(base)) {
    return {
      ok: true,
      warning: `Naming it '${base}' shadows a system binary — the run output and the `
        + "customer's own triage will both be hard to read. A neutral name is fine; this one is not.",
    }
  }
  return { ok: true }
}

/**
 * Per-step command preview under a composed destination.
 *
 * There is no server-side `rewritten_command` endpoint, so this substitution is
 * computed HERE, for display, and the panel says so. It reports three distinct
 * shapes, and the third one is the finding:
 *
 *   `adapter_token`  the step invokes `{adapter:TOOL-X}` — SimCore renders that
 *                    to the composed destination at dispatch, so the rename
 *                    takes effect.
 *   `path_literal`   the step names the artifact's default path literally. The
 *                    substitution below is what the composed plan produces.
 *   `inline_fetch`   THE STEP DOWNLOADS THE TOOL ITSELF (a literal curl/wget to
 *                    a public URL). Composing a rename does NOT change that
 *                    command: the target still reaches the internet and the file
 *                    still lands under its own name. Silently showing a pretty
 *                    "rewritten" line for such a step would claim a rename that
 *                    will not happen — so it is flagged instead.
 */
export function previewSteps({ scenario, adapterId, payloadName, destPath, defaultPath }) {
  const tokens = filenameTokens(payloadName)
  const adapterToken = adapterId ? `{adapter:${adapterId}}` : null
  const fromPath = defaultPath || (payloadName ? `/tmp/${payloadName}` : null)
  const out = []

  for (const step of scenario?.steps || []) {
    const cmd = String(step?.command || '')
    if (!cmd) continue
    const lower = cmd.toLowerCase()
    const mentions = tokens.some((t) => lower.includes(t))
      || (adapterToken && cmd.includes(adapterToken))
      || (fromPath && cmd.includes(fromPath))
    if (!mentions) continue

    const usesAdapterToken = Boolean(adapterToken && cmd.includes(adapterToken))
    // A URL whose last segment is the artifact — i.e. the step fetches it itself.
    const inlineFetch = tokens.some((t) => new RegExp(`https?://\\S*${escapeRe(t)}`, 'i').test(cmd))

    let rewritten = cmd
    const substitutions = []
    if (usesAdapterToken) {
      rewritten = rewritten.split(adapterToken).join(destPath)
      substitutions.push({ from: adapterToken, to: destPath })
    }
    if (fromPath && rewritten.includes(fromPath) && fromPath !== destPath) {
      rewritten = rewritten.split(fromPath).join(destPath)
      substitutions.push({ from: fromPath, to: destPath })
    }

    out.push({
      step_id: step.id || null,
      name: step.name || '',
      original: cmd,
      rewritten,
      substitutions,
      shape: usesAdapterToken ? 'adapter_token' : inlineFetch ? 'inline_fetch' : 'path_literal',
      inlineFetch,
    })
  }
  return out
}

function escapeRe(s) { return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&') }

/** The neutral-alias suggestion: same directory grammar, nothing about the tool. */
export function neutralAliasFor(filename) {
  const ext = /\.[^.]+$/.exec(String(filename || ''))
  return `/tmp/.cache/sysinfo${ext ? ext[0] : ''}`
}
