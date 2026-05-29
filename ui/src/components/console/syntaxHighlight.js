/**
 * syntaxHighlight — minimal regex-based tokenisers for the TTP browser
 * detection body blocks (XQL / Sigma-YAML).
 *
 * Why a custom 1-KB tokeniser instead of highlight.js / shiki / prism?
 *
 *   * XQL has a small, well-defined keyword set. A hand-written tokeniser
 *     covers it correctly with ~50 LOC and no bundle dependency.
 *   * shiki + WASM grammars add ~200 KB to the bundle; highlight.js core +
 *     a sql language pack is ~14 KB gzipped. Both miss XQL specifics
 *     (``preset``, ``alter``, ``comp``) so we'd patch them anyway.
 *   * Custom tokenisation gives us direct control over the Cortex theme —
 *     keywords land on a teal accent, strings on the navy-rest tone, no
 *     theming arbitration with a third-party stylesheet.
 *
 * Each tokeniser returns ``{type, text}[]`` and never collapses whitespace
 * or modifies the input — the rendered `<pre>` is byte-identical to the
 * raw body once spans are stripped, so copy semantics stay intact.
 */

// ---------------------------------------------------------------------------
// XQL — Cortex Query Language
// ---------------------------------------------------------------------------

// Keywords culled from public XQL docs + the corpus' shipped queries.
// Match is case-insensitive and word-bounded; the renderer preserves the
// original casing the operator typed.
const XQL_KEYWORDS = new Set([
  'preset', 'dataset',
  'filter', 'fields', 'sort', 'limit', 'comp', 'alter', 'join', 'union',
  'bin', 'window', 'config', 'arrayexpand', 'arraycreate', 'iploc',
  'and', 'or', 'not', 'in', 'contains', 'incidr', 'between',
  'asc', 'desc', 'by', 'on', 'as', 'over', 'where',
  'true', 'false', 'null', 'enum',
  'if', 'else', 'case', 'when', 'then', 'end',
])

const XQL_BUILTINS = new Set([
  'count', 'sum', 'avg', 'min', 'max', 'first', 'last', 'values', 'arrayagg',
  'to_timestamp', 'current_time', 'lowercase', 'uppercase',
  'replace', 'substr', 'regexp_match', 'regexp_replace', 'split',
  'json_extract_scalar', 'json_extract_array',
])

/**
 * Tokenise an XQL string into render-ready spans.
 *
 * @param {string} src
 * @returns {Array<{type: string, text: string}>}
 */
export function highlightXql(src) {
  if (!src) return []
  const tokens = []
  let i = 0
  const n = src.length

  while (i < n) {
    const ch = src[i]

    // Line comment ``// ...``
    if (ch === '/' && src[i + 1] === '/') {
      let j = i + 2
      while (j < n && src[j] !== '\n') j++
      tokens.push({ type: 'comment', text: src.slice(i, j) })
      i = j
      continue
    }

    // String literal — XQL accepts both " and ' quotes.
    if (ch === '"' || ch === "'") {
      const quote = ch
      let j = i + 1
      while (j < n && src[j] !== quote) {
        if (src[j] === '\\' && j + 1 < n) j += 2
        else j++
      }
      if (j < n) j++  // include the closing quote
      tokens.push({ type: 'string', text: src.slice(i, j) })
      i = j
      continue
    }

    // Number literal (integer or decimal)
    if (/[0-9]/.test(ch)) {
      let j = i
      while (j < n && /[0-9.]/.test(src[j])) j++
      tokens.push({ type: 'number', text: src.slice(i, j) })
      i = j
      continue
    }

    // Identifier / keyword
    if (/[A-Za-z_]/.test(ch)) {
      let j = i
      while (j < n && /[A-Za-z0-9_.]/.test(src[j])) j++
      const word = src.slice(i, j)
      const lower = word.toLowerCase()
      let type = 'ident'
      if (XQL_KEYWORDS.has(lower)) type = 'keyword'
      else if (XQL_BUILTINS.has(lower)) type = 'builtin'
      tokens.push({ type, text: word })
      i = j
      continue
    }

    // Operators / punctuation — collapse runs of the same char-class so
    // ``|`` stays one token but ``= "x"`` doesn't merge with the next ident.
    if (/[|=<>!+\-*/%&,;:()\[\]{}]/.test(ch)) {
      let j = i
      while (j < n && /[|=<>!+\-*/%&,;:()\[\]{}]/.test(src[j])) j++
      tokens.push({ type: 'operator', text: src.slice(i, j) })
      i = j
      continue
    }

    // Whitespace + everything else — passthrough.
    let j = i
    while (
      j < n &&
      !/[A-Za-z_0-9"'/|=<>!+\-*/%&,;:()\[\]{}]/.test(src[j])
    ) j++
    tokens.push({ type: 'plain', text: src.slice(i, j) })
    i = j
  }

  return tokens
}

// ---------------------------------------------------------------------------
// YAML — analytics module bodies + Sigma rules
// ---------------------------------------------------------------------------

/**
 * Tokenise a YAML document into render-ready spans. Recognises:
 *
 *   * ``# comment`` to end-of-line
 *   * top-level keys (``key:``) and indented keys
 *   * quoted string values
 *   * boolean / null literals (``true``, ``false``, ``null``)
 *   * numeric scalars
 *
 * This is deliberately permissive — we only need enough structure to make
 * Sigma rules and Cortex analytics modules legible, not to validate YAML.
 */
export function highlightYaml(src) {
  if (!src) return []
  const tokens = []
  // Process line by line so key:value detection is cheap.
  let cursor = 0
  while (cursor < src.length) {
    let eol = src.indexOf('\n', cursor)
    if (eol === -1) eol = src.length
    const line = src.slice(cursor, eol)
    tokens.push(..._tokeniseYamlLine(line))
    if (eol < src.length) {
      tokens.push({ type: 'plain', text: '\n' })
    }
    cursor = eol + 1
  }
  return tokens
}

function _tokeniseYamlLine(line) {
  const out = []

  // Leading indent.
  const indentMatch = /^(\s*)/.exec(line)
  const indent = indentMatch ? indentMatch[1] : ''
  if (indent) out.push({ type: 'plain', text: indent })
  let rest = line.slice(indent.length)

  // List-item bullet.
  if (rest.startsWith('- ')) {
    out.push({ type: 'operator', text: '- ' })
    rest = rest.slice(2)
  } else if (rest === '-') {
    out.push({ type: 'operator', text: '-' })
    return out
  }

  // Trailing inline comment — splice it off and recurse the head.
  const commentIdx = _findCommentStart(rest)
  let comment = ''
  if (commentIdx !== -1) {
    comment = rest.slice(commentIdx)
    rest = rest.slice(0, commentIdx)
  }

  // ``key: value`` — key gets the keyword colour, value tokenised by type.
  const colonIdx = _findUnquotedColon(rest)
  if (colonIdx !== -1) {
    const key = rest.slice(0, colonIdx)
    const after = rest.slice(colonIdx + 1)
    if (/^[A-Za-z_][A-Za-z0-9_\-.]*$/.test(key)) {
      out.push({ type: 'keyword', text: key })
      out.push({ type: 'operator', text: ':' })
      out.push(..._tokeniseYamlValue(after))
    } else {
      out.push({ type: 'plain', text: rest })
    }
  } else if (rest) {
    out.push(..._tokeniseYamlValue(rest))
  }

  if (comment) {
    out.push({ type: 'comment', text: comment })
  }
  return out
}

function _findCommentStart(s) {
  // ``#`` starts a comment unless it's inside quotes.
  let inQuote = null
  for (let i = 0; i < s.length; i++) {
    const c = s[i]
    if (inQuote) {
      if (c === inQuote) inQuote = null
    } else if (c === '"' || c === "'") {
      inQuote = c
    } else if (c === '#' && (i === 0 || /\s/.test(s[i - 1]))) {
      return i
    }
  }
  return -1
}

function _findUnquotedColon(s) {
  let inQuote = null
  for (let i = 0; i < s.length; i++) {
    const c = s[i]
    if (inQuote) {
      if (c === inQuote) inQuote = null
    } else if (c === '"' || c === "'") {
      inQuote = c
    } else if (c === ':') {
      return i
    }
  }
  return -1
}

function _tokeniseYamlValue(raw) {
  const out = []
  // Preserve the space between the colon and the value as plain so we
  // round-trip the original whitespace.
  const leading = /^(\s*)/.exec(raw)?.[1] || ''
  if (leading) out.push({ type: 'plain', text: leading })
  const v = raw.slice(leading.length)
  if (!v) return out

  if (v === 'true' || v === 'false' || v === 'null' ||
      v === 'True' || v === 'False' || v === 'Null') {
    out.push({ type: 'builtin', text: v })
    return out
  }
  if (/^-?[0-9]+(\.[0-9]+)?$/.test(v)) {
    out.push({ type: 'number', text: v })
    return out
  }
  if ((v.startsWith('"') && v.endsWith('"') && v.length >= 2) ||
      (v.startsWith("'") && v.endsWith("'") && v.length >= 2)) {
    out.push({ type: 'string', text: v })
    return out
  }
  out.push({ type: 'plain', text: v })
  return out
}

// ---------------------------------------------------------------------------
// Public language selector
// ---------------------------------------------------------------------------

/**
 * Pick the right tokeniser for a TTP detection ``kind``.
 *
 * @param {string} kind  ``biocs`` | ``xql_queries`` | ``correlation_rules``
 *                       | ``iocs`` | ``analytics_modules``
 * @returns {(src: string) => Array<{type: string, text: string}>}
 */
export function tokeniserFor(kind) {
  if (kind === 'analytics_modules') return highlightYaml
  // BIOC / XQL / correlation logic is XQL-shaped; IOC values are not
  // really code but the XQL tokeniser falls back to ``plain`` runs for
  // arbitrary strings so it's a safe default.
  return highlightXql
}
