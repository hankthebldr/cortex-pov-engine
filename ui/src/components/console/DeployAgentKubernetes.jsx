import React, { useState, useCallback } from 'react'
import { agentManifestUrl, mintEnrollmentToken } from '../../api/client.js'

void React

// Same pattern the server enforces on `authorized_by`; checking it here keeps
// the console from rendering a URL the server will reject.
const AUTHORIZED_BY_RE = /^[A-Za-z0-9 ._@+'()-]{1,120}$/

const PROFILES = [
  ['baseline', 'baseline'],
  ['cluster-privilege', 'cluster-privilege'],
  ['lab-privileged', 'lab-privileged'],
]

const CONSENT_LABELS = {
  cluster_privilege_authorized: 'Bind a wildcard ClusterRole to the agent',
  node_access_authorized: "Privileged pod with host PID and the node's root mounted read-only",
}

const REQUIRED_CONSENT = {
  baseline: [],
  'cluster-privilege': ['cluster_privilege_authorized'],
  'lab-privileged': ['cluster_privilege_authorized', 'node_access_authorized'],
}

/**
 * Kubernetes tab of the deploy-agent dialog: the manifest command first, the
 * Secret command second. The minted token appears only in the Secret command.
 */
export default function DeployAgentKubernetes({ origin = '', copyText = () => {} }) {
  const [profile, setProfile] = useState('baseline')
  const [consent, setConsent] = useState({})
  const [authorizedBy, setAuthorizedBy] = useState('')
  const [token, setToken] = useState(null)
  const [minting, setMinting] = useState(false)
  const [mintError, setMintError] = useState(null)

  const required = REQUIRED_CONSENT[profile] || []
  const gated = required.length > 0
  const name = authorizedBy.trim()
  const nameInvalid = gated && name !== '' && !AUTHORIZED_BY_RE.test(name)
  const consentComplete = required.every((k) => consent[k] === true)
  const ready = !gated || (consentComplete && name !== '' && !nameInvalid)

  const manifestUrl = ready
    ? `${origin}${agentManifestUrl({
        profile,
        consent,
        authorizedBy: gated ? name : undefined,
      })}`
    : null
  const manifestCmd = manifestUrl ? `kubectl apply -f '${manifestUrl}'` : null
  const secretCmd = token
    ? `kubectl -n cortexsim-agent create secret generic cortexsim-enroll --from-literal=server=${origin} --from-literal=token=${token.token}`
    : null

  const mint = useCallback(async () => {
    setMinting(true); setMintError(null)
    try {
      setToken(await mintEnrollmentToken({
        label: 'kubernetes', ttl_seconds: 43200, max_uses: 50,
      }))
    } catch (err) {
      setMintError(err.message || 'Could not mint an enrollment token')
    } finally {
      setMinting(false)
    }
  }, [])

  return (
    <div data-testid="deploy-kubernetes">
      <div className="deploy-field">
        <label className="deploy-field">
          <span className="launch-field__label">Profile</span>
          <select
            className="launch-select"
            aria-label="Profile"
            value={profile}
            onChange={(e) => setProfile(e.target.value)}
          >
            {PROFILES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
      </div>

      {gated && (
        <div className="deploy-field">
          {required.map((key) => (
            <label key={key} className="deploy-hint">
              <input
                type="checkbox"
                checked={consent[key] === true}
                onChange={(e) => setConsent((c) => ({ ...c, [key]: e.target.checked }))}
              />{' '}
              {CONSENT_LABELS[key]}
            </label>
          ))}
          <label className="deploy-field">
            <span className="launch-field__label">Authorized by</span>
            <input
              className="launch-select deploy-input"
              aria-label="Authorized by"
              value={authorizedBy}
              onChange={(e) => setAuthorizedBy(e.target.value)}
              maxLength={120}
              spellCheck={false}
            />
          </label>
          {nameInvalid && (
            <p className="deploy-error" role="alert">
              Authorized by may contain letters, digits, spaces and . _ @ + ' ( ) - only (1-120 characters).
            </p>
          )}
        </div>
      )}

      <div className="deploy-field">
        <span className="launch-field__label">1. Apply the manifest</span>
        {manifestCmd ? (
          <div className="deploy-snippet">
            <code data-testid="deploy-k8s-manifest">{manifestCmd}</code>
            <button type="button" className="btn btn--xs" onClick={() => copyText(manifestCmd)}>Copy</button>
          </div>
        ) : (
          <p className="deploy-hint" data-testid="deploy-k8s-manifest-blocked">
            Tick each capability and name who authorised it to get the command.
          </p>
        )}
      </div>

      <div className="deploy-field">
        <span className="launch-field__label">2. Create the enrollment Secret</span>
        {secretCmd ? (
          <div className="deploy-snippet">
            <code data-testid="deploy-k8s-secret">{secretCmd}</code>
            <button type="button" className="btn btn--xs" onClick={() => copyText(secretCmd)}>Copy</button>
          </div>
        ) : (
          <>
            {mintError && <p className="deploy-error" role="alert">{mintError}</p>}
            <div className="deploy-actions">
              <button type="button" className="btn btn--primary" onClick={mint} disabled={minting}>
                {minting ? 'Minting…' : 'Mint enrollment token ▸'}
              </button>
            </div>
          </>
        )}
      </div>

      <p className="deploy-hint">
        The pod pulls the beacon from this SimCore when it starts. A SimCore address the
        cluster cannot reach leaves the pod in Init:Error.
      </p>
      <p className="deploy-hint">
        This pod runs commands sent by this SimCore. It removes itself after 12 hours.
      </p>
    </div>
  )
}
