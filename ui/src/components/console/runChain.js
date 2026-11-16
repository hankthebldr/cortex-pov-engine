/**
 * runChain.js — where a lane on Run detail's chain leads (design v26 §4.6,
 * §9.3). Pure, no React.
 *
 * What each card SAYS is the runtime log's (`runtimeLog.buildRuntimeLog`): a
 * card and its log block read the same host record, in execution words only.
 */
import { componentDestination } from './searchIndex.js'

/**
 * The component behind each signal-path lane, for the lane-header button. Keys
 * are the component table's (`componentModel.js`), so the rail, the header,
 * ⌘K and the run chain all open the same page for a component. Every lane
 * ChainStage turns into a button has a destination, so none is a dead end.
 */
const LANE_COMPONENT = {
  ENDPOINT: 'agent',    // the beacon runs it; the Cortex Agent sees the process
  NONE: 'agent',        // the step only logs or waits, on the beacon
  COLLECTOR: 'http',    // records POSTed to an HTTP collector or Broker VM
  WIRE: 'ngfw',         // a sensor on the wire: the NGFW
  CLOUD: 'tenant',      // the provider's audit log, ingested by the tenant
  ENG: 'tenant',        // tenant content: correlation rules
}

/** The route ({ destination, params? }) behind a lane, or null. */
export function laneDestination(laneKey, isValid = () => true) {
  const key = LANE_COMPONENT[laneKey]
  return key ? componentDestination(key, isValid) : null
}
