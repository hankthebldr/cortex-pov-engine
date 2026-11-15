import React from 'react'
import { useEnvironment } from '../../../context/EnvironmentContext.jsx'
import ComponentPage from './ComponentPage.jsx'
import ConfigureFrame from './ConfigureFrame.jsx'
import useComponentStatus from '../useComponentStatus.js'
import { componentByKey } from '../componentModel.js'
import { useConfirmed } from '../useSetupProgress.js'

/**
 * Broker VM, XDR Collector and Cortex Engine — the components SimCore has no
 * API for (design v26 §9.2: "you confirmed" / Unknown, never green).
 *
 * Each gets the one page shape with what it can honestly hold: the Setup
 * checklist (every step is the DC's or Cortex's to show, so every step is
 * ticked), Configuration rows that read "You confirmed" from those ticks or
 * Can't check, no Services (nothing to read), no management tab (v26: "the
 * others have none"), and no Test — a button whose only possible answer is
 * Can't check would be a button without an action.
 *
 * Step ids match COMPONENT_STEPS in componentModel.js: the rail's status for
 * these components is "You confirmed" once every one is ticked.
 *
 * Copy is the design track's (v26 SETUP tables) with its sample values
 * removed. The XDR Collector and Cortex Engine steps are the handoff's lower-
 * confidence ones; their installer commands are left out rather than shipped
 * unverified.
 */

const STEPS = {
  bvm: [
    {
      id: 'image',
      title: 'Download the image',
      why: 'Pick the image for the hypervisor the customer runs it on.',
      where: 'Cortex › Settings › Configurations › Data Broker › Broker VMs › Add Broker',
      verifier: 'you',
      fields: [['Image', 'OVA (ESXi) · VHD (Hyper-V, Azure) · QCOW2 (KVM, Nutanix) · VMDK (AWS, GCP)']],
    },
    {
      id: 'deploy',
      title: 'Deploy and set the network',
      why: 'Deploy it thin-provisioned. Use DHCP or a static address (IP, netmask, gateway, DNS); only one interface carries a default gateway.',
      where: 'hypervisor · serial console',
      verifier: 'you',
      fields: [['Address', 'the Broker VM\'s address on the lab network']],
    },
    {
      id: 'console',
      title: 'Open the web console',
      why: 'Sign in with the default password, then set a new one.',
      where: 'https://<broker-address>:4443',
      verifier: 'you',
    },
    {
      id: 'register',
      title: 'Register with a token',
      why: 'Generate a token in Cortex and paste it in the Broker VM\'s web console. The broker then shows Connected in Cortex. POVengine has no read of it.',
      where: 'Broker VMs › Add Broker › Generate Token',
      verifier: 'cortex',
      fields: [['Token', 'paste once']],
    },
    {
      id: 'syslog',
      title: 'Activate the Syslog Collector',
      why: 'In the broker\'s APPS column: Add › Syslog Collector. Keep 514/UDP or add TCP. Save — the app shows a green dot in Cortex.',
      where: 'Broker VMs › APPS › Add › Syslog Collector',
      verifier: 'cortex',
      fields: [['Protocol / port', 'UDP 514 (default)'], ['Format', 'CEF for the firewall · Auto-Detect otherwise']],
    },
    {
      id: 'reach',
      title: 'Check agents can reach it',
      why: 'Network-plane scenarios send their traffic from the agents to the broker\'s syslog port. POVengine has no probe for this yet, so it takes your word.',
      verifier: 'you',
      fields: [['From', 'each agent host → the Broker VM, port 514']],
    },
  ],
  xdrc: [
    {
      id: 'installer',
      title: 'Create the installer',
      why: 'Create an installation package for the log host\'s OS. XDR Collectors need the Data Collection add-on.',
      where: 'Cortex › Settings › Configurations › XDR Collectors › Installers',
      verifier: 'you',
      fields: [['Package', 'Windows · Linux']],
    },
    {
      id: 'install',
      title: 'Install on the host',
      why: 'Run the installer elevated on the log host. The collector appears in the tenant when it calls home; POVengine has no read of the collectors list.',
      where: 'log host',
      verifier: 'cortex',
    },
    {
      id: 'profile',
      title: 'Create a collector profile',
      why: 'What to collect: Filebeat on Linux, Winlogbeat on Windows.',
      where: 'XDR Collectors › Profiles',
      verifier: 'you',
    },
    {
      id: 'policy',
      title: 'Apply it with a policy',
      why: 'A collection machine policy assigns the profile to the host.',
      where: 'XDR Collectors › Policies',
      verifier: 'cortex',
    },
    {
      id: 'arrive',
      title: 'Confirm data arrives',
      why: 'Look for the host\'s rows in the dataset its profile writes.',
      where: 'Cortex › XQL',
      verifier: 'cortex',
    },
  ],
  engine: [
    {
      id: 'create',
      title: 'Create the engine in Cortex',
      why: 'Create a new engine and download its installer for the host.',
      where: 'Cortex › Settings › Configurations › Engines › Create New Engine',
      verifier: 'you',
    },
    {
      id: 'install',
      title: 'Install it on a host',
      why: 'Run the installer as root on a Linux host inside the network. The engine shows Connected in Cortex when it calls home; POVengine has no read of it.',
      where: 'engine host',
      verifier: 'cortex',
      fields: [['Outbound', '443 to the tenant only']],
    },
    {
      id: 'run',
      title: 'Run the integrations on it',
      why: 'For each integration that must run inside the network, set Run on to this engine.',
      where: 'Cortex › integration instance › Run on',
      verifier: 'you',
    },
  ],
}

// The Configuration rows: what has to be true, and the step that makes it so.
const CONFIG = {
  bvm: [['register', 'Registered and connected in Cortex'], ['syslog', 'Syslog Collector applet active'], ['reach', 'Agents reach it on 514']],
  xdrc: [['install', 'Collector installed on the log host'], ['profile', 'A profile collects the POV sources'], ['policy', 'A policy applies the profile'], ['arrive', 'Rows arrive for the host']],
  engine: [['install', 'Engine installed and connected'], ['run', 'Integrations run on it']],
}

const NO_READ = {
  bvm: 'POVengine has no read of a Broker VM. What it shows is what you confirm in Setup.',
  xdrc: 'POVengine has no read of an XDR Collector. What it shows is what you confirm in Setup.',
  engine: 'POVengine has no read of a Cortex Engine. What it shows is what you confirm in Setup.',
}

function confirmedComponent(key, testId) {
  return function ConfirmedComponent({ params = {}, setParams = () => {}, onNavigate = () => {} }) {
    const env = useEnvironment()
    const { statuses } = useComponentStatus()
    const confirmed = useConfirmed()
    const meta = componentByKey(key)
    const steps = STEPS[key]
    const config = CONFIG[key].map(([step, label]) => ({
      id: step,
      label,
      state: confirmed[`${key}:${step}`] ? 'confirmed' : 'cant_check',
      drill: { tab: 'setup', step },
    }))
    return (
      <ComponentPage
        componentKey={key}
        name={meta.name}
        icon={meta.icon}
        status={statuses[key]}
        services={[]}
        servicesEmpty={NO_READ[key]}
        config={config}
        setup={() => (
          <ConfigureFrame
            confirmPrefix={key}
            steps={steps}
            current={params.step || null}
            onSelect={(step) => setParams({ step })}
            ctx={{ env }}
          />
        )}
        params={params}
        setParams={setParams}
        onNavigate={onNavigate}
        testId={testId}
      />
    )
  }
}

export const BrokerVmComponent = confirmedComponent('bvm', 'component-bvm')
export const XdrCollectorComponent = confirmedComponent('xdrc', 'component-xdrc')
export const CortexEngineComponent = confirmedComponent('engine', 'component-engine')
