/**
 * ComposerPalette — the LEFT column of the Simulation Composer.
 *
 * It is the reference tool's roles/agents/skills/plugins/mcps rail, re-cast for
 * CortexSim and organized by NICE (Network · Identity · Cloud · Endpoint). The
 * DC reaches into it to add to the chain: a step kind, a whole library scenario
 * (seed/replace), a TTP card (appended pre-bound to a detection — the path that
 * satisfies the launch gate), a tool adapter (`adapter_ref` on the selected
 * step), a target beacon, or a staged shelf payload.
 *
 * It is a DUMB VIEW. It never fetches and never decides what a group contains:
 * the container (`ComposerView`) assembles `groups` from the real environment,
 * the shelf, and the API clients (`getTtps`, `getToolAdapters`) exactly as the
 * old `bench` memo did, and hands them down. Every item carries its own `add()`
 * callback; clicking an item is the palette's only side effect, and even that
 * is the container's function, not the palette's.
 *
 * What the palette itself owns is presentation only: the NICE tab bar and the
 * two filters that narrow what is shown — the active tab (via each group's
 * `tab` field) and the free-text query (over each item's name, meta and tags).
 * Tag chips are a shortcut into that query: a chip is a tag the active tab's
 * items carry, and clicking it filters to the tag. Nothing here mutates
 * anything; all of it is a pure function of props, so the same groups always
 * render the same rail.
 *
 * CSS is unchanged from the `bench` era on purpose: the class names
 * `.composer-bench`, `.composer-bench__group`, `.bench-item` (and friends) stay
 * verbatim so `composer.css` needs no edit — "bench" was renamed to "palette"
 * in the component tree, not in the stylesheet.
 */
import React, { useMemo } from 'react'
import { Icon } from './kit/index.js'

// A chip row longer than this stops being a shortcut and becomes a second list.
const MAX_TAG_CHIPS = 10

/**
 * @param {Object}   props
 * @param {Array}    [props.tabs]      NICE-organized tab descriptors `[{id,label,icon?}]`. A tab
 *                                     with an `icon` is drawn as the icon alone, named by `label`.
 *                                     When empty/absent, no tab bar renders and every group shows.
 * @param {string}   [props.activeTab] The id of the selected tab; groups whose `tab` differs are hidden.
 * @param {Function} [props.onTab]     `(id) => void` — the container flips `activeTab`.
 * @param {Array}    props.groups      `[{label, tone, tab?, items:[{key,name,meta,add,disabled?,title?,tags?,starred?}]}]`.
 *                                     `starred` marks one of the system's best scenarios (★).
 *                                     All content is API-sourced upstream; the palette never invents a group.
 * @param {string}   [props.query]     Free-text filter over each item's `name` + `meta`.
 * @param {Function} [props.onQuery]   `(str) => void` — the container owns the query string.
 * @param {boolean}  [props.loading]   Optional: a surface is still fetching. Shown as a quiet hint,
 *                                      never as a fabricated-empty rail.
 */
export default function ComposerPalette({
  tabs = [],
  activeTab = null,
  onTab = () => {},
  groups = [],
  query = '',
  onQuery = () => {},
  loading = false,
  // Composer mode: when true, each item is draggable onto the canvas (its `key`
  // rides the drag as `application/cortexsim-node`, which the canvas resolves to
  // the right create/attach action). Clicking still runs `add()` as before, so
  // the two input modes coexist and nothing changes for the windowed caller.
  draggable = false,
}) {
  const q = query.trim().toLowerCase()

  // Two pure narrowings, in order: keep groups on the active tab (a group with
  // no `tab` is tab-agnostic and always shows), then keep items matching the
  // query. Empty groups drop out so the rail never shows a bare title.
  const tabGroups = useMemo(
    () => groups.filter((g) => !tabs.length || !g.tab || g.tab === activeTab),
    [groups, tabs.length, activeTab],
  )

  const visibleGroups = useMemo(() => {
    const match = (it) => !q
      || `${it.name ?? ''} ${it.meta ?? ''} ${(it.tags || []).join(' ')}`.toLowerCase().includes(q)
    return tabGroups
      .map((g) => ({ ...g, items: (g.items || []).filter(match) }))
      .filter((g) => g.items.length)
  }, [tabGroups, q])

  // The tags this tab's items carry, most common first. Tags that differ only
  // in case are one chip (the ANALYTICS plane and the Analytics detection type
  // filter the same way). A tag every item shares narrows nothing, so it is
  // not offered.
  const tagChips = useMemo(() => {
    const tagged = tabGroups.flatMap((g) => g.items || []).filter((it) => it.tags?.length)
    const chips = new Map()   // lowercased tag -> { label, n }
    for (const it of tagged) {
      for (const key of new Set(it.tags.map((t) => t.toLowerCase()))) {
        const chip = chips.get(key) || { label: it.tags.find((t) => t.toLowerCase() === key), n: 0 }
        chip.n += 1
        chips.set(key, chip)
      }
    }
    return [...chips.values()]
      .filter((c) => c.n < tagged.length)
      .sort((a, b) => b.n - a.n || a.label.localeCompare(b.label))
      .slice(0, MAX_TAG_CHIPS)
      .map((c) => c.label)
  }, [tabGroups])

  const nothingShown = !visibleGroups.length

  return (
    <aside className="composer-bench" aria-label="Composer palette" data-testid="composer-palette">
      <input
        className="composer-bench__filter"
        value={query}
        onChange={(e) => onQuery(e.target.value)}
        placeholder="Filter the palette…"
        aria-label="Filter the palette"
      />

      {tabs.length > 0 && (
        <div className="composer-bench__tabs" role="tablist" aria-label="Palette surfaces">
          {tabs.map((t) => (
            <button
              type="button"
              key={t.id}
              role="tab"
              aria-selected={t.id === activeTab}
              data-testid={`palette-tab-${t.id}`}
              className={
                'composer-bench__tab' + (t.id === activeTab ? ' composer-bench__tab--active' : '')
              }
              onClick={() => onTab(t.id)}
              aria-label={t.icon ? t.label : undefined}
              title={t.icon ? t.label : undefined}
            >
              {t.icon ? <Icon name={t.icon} size={13} /> : t.label}
            </button>
          ))}
        </div>
      )}

      {tagChips.length > 0 && (
        <div className="composer-bench__tags" role="group" aria-label="Filter by tag">
          {tagChips.map((t) => {
            const on = q === t.toLowerCase()
            return (
              <button
                type="button"
                key={t}
                className={'composer-bench__tag' + (on ? ' composer-bench__tag--on' : '')}
                aria-pressed={on}
                onClick={() => onQuery(on ? '' : t)}
              >
                {t}
              </button>
            )
          })}
        </div>
      )}

      {visibleGroups.map((group) => (
        <div className="composer-bench__group" key={group.label}>
          <div className="composer-bench__group-title">{group.label}</div>
          {group.items.map((it) => (
            <button
              type="button"
              key={it.key}
              className={'bench-item' + (draggable && !it.disabled ? ' bench-item--draggable' : '')}
              onClick={it.add}
              disabled={it.disabled}
              title={it.title || it.meta}
              draggable={draggable && !it.disabled}
              onDragStart={draggable && !it.disabled ? (e) => {
                e.dataTransfer.setData('application/cortexsim-node', it.key)
                e.dataTransfer.effectAllowed = 'copy'
              } : undefined}
            >
              {it.starred
                ? <Icon name="star" size={11} className="bench-item__star" />
                : (
                  <span
                    className={`bench-item__dot bench-item__dot--${group.tone}`}
                    aria-hidden="true"
                  />
                )}
              <span className="bench-item__text">
                <span className="bench-item__name">{it.name}</span>
                <span className="bench-item__meta mono">{it.meta}</span>
              </span>
              <span className="bench-item__add" aria-hidden="true">+</span>
            </button>
          ))}
        </div>
      ))}

      {nothingShown && loading && (
        <div className="composer-bench__empty">loading…</div>
      )}
      {nothingShown && !loading && (
        <div className="composer-bench__empty">
          {q ? <>nothing matches “{query}”</> : 'nothing on this tab yet'}
        </div>
      )}
    </aside>
  )
}
