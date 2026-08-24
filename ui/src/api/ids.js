/**
 * ids.js — the ONE place the console resolves an API record's identity.
 *
 * SimCore's ORM rows carry two identities and `to_dict()` emits both:
 *
 *     Agent → { id: 1 (int PK), agent_id: "jumpbox-01" (string) }
 *     Run   → { id: 7 (int PK), run_id: "4ee09860-…" (uuid string) }
 *
 * Every API path and request body is keyed on the STRING one — the routers
 * look records up by `Agent.agent_id` / `Run.run_id`, and `RunRequest`
 * declares `target_agent_id: str`. The integer `id` is a database detail that
 * never appears in a URL.
 *
 * Reading `r.id || r.run_id` therefore picks the wrong key on every record the
 * API returns: pull-mode launches posted `target_agent_id: 1` and came back
 * 422, and `#/runs?run=7` resolved against nothing. The ordering below is
 * load-bearing — resolve the string identity FIRST, and only fall back to the
 * PK for records that genuinely lack one (optimistic local rows, fixtures).
 *
 * Compare ids as strings: a hash-router param is always a string, so a record
 * that fell back to a numeric PK would otherwise never match its own route.
 */

/** Canonical agent identity — the value `target_agent_id` and `/api/agents/{id}` want. */
export function agentIdOf(agent) {
  if (!agent) return null
  const id = agent.agent_id ?? agent.id
  return id == null ? null : String(id)
}

/** Canonical run identity — the value every `/api/runs/{run_id}/…` path wants. */
export function runIdOf(run) {
  if (!run) return null
  const id = run.run_id ?? run.id
  return id == null ? null : String(id)
}

/** Canonical scenario identity (`SIM-EDR-001`), for symmetry at call sites. */
export function scenarioIdOf(scenario) {
  if (!scenario) return null
  const id = scenario.scenario_id ?? scenario.id
  return id == null ? null : String(id)
}

/** True when `record`'s canonical id equals `candidate` (string comparison). */
export function idMatches(recordId, candidate) {
  if (recordId == null || candidate == null) return false
  return String(recordId) === String(candidate)
}
