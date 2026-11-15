/**
 * componentAbout.js — the four plain lines at the top of every component page
 * (design v33, CHANGE C7): what it is, why the POV needs it, what it unlocks,
 * where it lives.
 *
 * Ported from the canvas's COMPONENT_ABOUT and held to what the code does:
 *   - POVengine reads the tenant and the firewall and never writes to either.
 *   - SimCore has no API for a Broker VM, an XDR Collector or a Cortex Engine,
 *     so nothing here says POVengine checks them; their pages read "You
 *     confirmed" or Can't check.
 *   - "What it unlocks" names what the scenario library actually routes
 *     through the component. Where nothing is routed through it, it says so.
 *
 * Keyed by the component ids in componentModel.js. An id with no entry gets
 * no About block: a page should not borrow another component's words.
 */

export const ABOUT_LINES = Object.freeze([
  ['what', 'What it is'],
  ['why', 'Why the POV needs it'],
  ['unlocks', 'What it unlocks'],
  ['where', 'Where it lives'],
])

export const COMPONENT_ABOUT = Object.freeze({
  pov: {
    what: 'The engine itself: the SimCore API, this console and the run queue, running in Docker on the jumpbox.',
    why: 'Every run is dispatched and recorded here, and read back from here once a tenant is connected.',
    unlocks: 'Everything. Nothing else works until it is up.',
    where: 'Jumpbox · Docker Compose · port 8888 by default',
  },
  agent: {
    what: "The POVengine beacon on each target host. It runs beside the customer's Cortex XDR agent, which POVengine does not install.",
    why: 'Beacons run the scenario steps; the XDR agent is what sees them.',
    unlocks: 'Pull runs: nearly every scenario step runs on an agent, whatever its plane. An offline bundle runs without one.',
    where: 'Target hosts · Linux, macOS, Windows, or a Kubernetes pod',
  },
  tenant: {
    what: "The customer's Cortex XSIAM / XDR tenant, reached with a read-only API key. POVengine never writes to it.",
    why: 'Read back asks the tenant which detections fired. Without a tenant, no run is read back.',
    unlocks: "Read-back: each run's expected detections checked against the tenant's own alerts.",
    where: 'Cortex › Settings › Configurations › Integrations › API Keys',
  },
  http: {
    what: 'A Cortex HTTP log collector. POVengine sends it shape-true third-party log records.',
    why: 'It lets the POV test detections on log sources the customer cannot replay on demand.',
    unlocks: 'Log-stream scenarios: identity sign-ins, email, cloud and Kubernetes audit, Microsoft 365, Windows, firewall, VPN and third-party alert logs.',
    where: 'Cortex › Settings › Data Sources & Integrations',
  },
  bvm: {
    what: 'The Cortex Broker VM inside the customer network, running the Syslog Collector applet.',
    why: 'Firewall logs sent by syslog, and other syslog sources, reach Cortex through it.',
    unlocks: 'Network (NDR) scenarios whose firewall logs go by syslog, and anything else sourced from syslog.',
    where: 'Customer network (VM image) · Cortex › Settings › Configurations › Data Broker › Broker VMs',
  },
  ngfw: {
    what: "The customer's Palo Alto Networks firewall, registered with a read-only key, and a POV test rule you add. POVengine never writes to it.",
    why: 'Egress and command-and-control steps have to be seen on the wire; its logs carry that.',
    unlocks: 'NDR egress and C2 test cases, and the endpoint-to-network stitch (network_story).',
    where: 'PAN-OS · logs go to a Broker VM or Strata Logging Service',
  },
  xdrc: {
    what: 'The XDR Collector: a light log shipper for hosts that do not run the full XDR agent.',
    why: 'It brings in host logs (web or application servers) that the XDR agent does not collect.',
    unlocks: 'Log-based detections on hosts without the XDR agent. POVengine maps no scenario to it yet.',
    where: 'The log host · Cortex › Settings › Configurations › XDR Collectors',
  },
  engine: {
    what: 'Cortex Engine: a customer-hosted engine that runs Cortex integrations close to the data.',
    why: 'An integration that must reach systems inside the network runs on an engine there, not in the cloud.',
    unlocks: 'Integrations that cannot run from the cloud. POVengine maps no scenario to it yet.',
    where: 'A Linux host in the customer network · Cortex › Settings › Configurations › Engines',
  },
})

/**
 * The four lines for one component, in order, or null when the id has no
 * entry (an own property only: 'constructor' is not a component).
 *
 * @param {string} key  a component id (componentModel.js COMPONENTS[].key)
 * @returns {{ key: string, label: string, text: string }[] | null}
 */
export function componentAbout(key) {
  if (typeof key !== 'string' || !Object.prototype.hasOwnProperty.call(COMPONENT_ABOUT, key)) return null
  const entry = COMPONENT_ABOUT[key]
  return ABOUT_LINES.map(([k, label]) => ({ key: k, label, text: entry[k] }))
}
