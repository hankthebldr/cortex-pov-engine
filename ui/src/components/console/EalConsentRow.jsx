/**
 * EalConsentRow — the Launch gate's consent for a scenario's EAL steps
 * (ealConsent.js): one compact row, shown only when the armed scenario has one.
 *
 *   Send live EAL traffic       unticked, the EAL steps run dry (as before)
 *   Authorized by               who authorised it; sent with the launch
 *   Includes C2-shaped traffic  the extra consent a C2-shaped plugin needs
 *
 * The console cannot tell which plugins SimCore counts as C2-shaped, so the
 * third control shows whenever the first does. Both follow the first: they
 * are sent only when it is ticked.
 *
 * Presentational: LaunchView owns the launch hook and passes the state in.
 */
import React from 'react'

void React

export default function EalConsentRow({
  live = false,
  onLive = () => {},
  authorizedBy = '',
  onAuthorizedBy = () => {},
  c2 = false,
  onC2 = () => {},
}) {
  return (
    <div className="launch-eal" data-testid="launch-eal-consent" role="group" aria-label="EAL traffic consent">
      <label className="launch-eal__tick" title="Unticked, the EAL steps run dry and nothing is sent">
        <input type="checkbox" checked={live} onChange={(e) => onLive(e.target.checked)} />
        <span>Send live EAL traffic</span>
      </label>
      <label className="launch-eal__who">
        <span>Authorized by</span>
        <input
          type="text"
          className="launch-eal__name"
          value={authorizedBy}
          disabled={!live}
          autoComplete="name"
          onChange={(e) => onAuthorizedBy(e.target.value)}
        />
      </label>
      <label className="launch-eal__tick" title="Needed when a step's plugin is C2-shaped, such as a beacon or a tunnel">
        <input type="checkbox" checked={c2} disabled={!live} onChange={(e) => onC2(e.target.checked)} />
        <span>Includes C2-shaped traffic</span>
      </label>
    </div>
  )
}
