/**
 * tourStops — the five-stop activation path.
 *
 * Deliberately NOT a tour of all eleven destinations. The goal is productive
 * use, not a map of the product. Copy says what a surface is FOR; it must never
 * imply a run proves detection efficacy.
 */
export const TOUR_STOPS = [
  {
    id: 'library',
    anchor: 'nav-library',
    destination: 'library',
    title: 'Start here',
    body: 'The Library holds 170 scenarios across 15 detection planes. Every POV starts by choosing one.',
  },
  {
    id: 'scenario',
    anchor: 'scenario-card-first',
    destination: 'library',
    title: 'A scenario is a TTP chain',
    body: 'Each card is an ordered set of steps plus the detections each step should trigger. Arm one to stage it for launch.',
  },
  {
    id: 'agents-empty',
    anchor: 'nav-agents',
    destination: 'agents',
    title: 'Nothing runs without a beacon',
    body: 'Agents is empty on a fresh install. Until one beacon checks in, a launch queues a task that nothing collects.',
  },
  {
    id: 'enroll',
    anchor: 'agent-enroll',
    destination: 'agents',
    title: 'Deploy one now',
    body: 'Mint an enrollment token and run the single line it gives you on the target host. SimCore assigns the agent id.',
  },
  {
    id: 'runs',
    anchor: 'nav-runs',
    destination: 'runs',
    title: 'Where the evidence lands',
    body: 'Runs & Proof collects each step’s output, the per-detection results, and the POV report you export for the customer.',
  },
]
