/**
 * Demo data for the private test environment.
 *
 * Everything the console shows today comes from here. It is kept in one file,
 * exported through named functions rather than constants, so there is exactly
 * one place to delete when the API is wired — and so nothing can quietly
 * half-migrate and leave a screen mixing real rows with invented ones.
 *
 * `DEMO_DATA` is exported so screens can label themselves honestly.
 */

export const DEMO_DATA = true;

export function fixtureAgents() {
  return [
    { agentId: 'eng', name: 'Ansel', role: 'Engineering', archetype: 'paper',
      color: '#2b6bff', state: 'working', budget: { perMonthUsd: 40 } },
    { agentId: 'ops', name: 'Pell', role: 'Cloud Operations', archetype: 'pebble',
      color: '#12a594', state: 'approval', budget: { perMonthUsd: 30 } },
    { agentId: 'cos', name: 'Wren', role: 'Chief of Staff', archetype: 'cloud',
      color: '#8b2fe0', state: 'thinking', budget: { perMonthUsd: 25 } },
    { agentId: 'res', name: 'Moss', role: 'Research', archetype: 'jelly',
      color: '#e8833a', state: 'idle', budget: { perMonthUsd: 20 } },
    { agentId: 'fin', name: 'Tally', role: 'Finance', archetype: 'lantern',
      color: '#e93d82', state: 'offline', budget: { perMonthUsd: 15 } },
  ];
}

export function fixtureInbox() {
  return [
    { id: 'apv-1', kind: 'approval', agent: 'Pell', archetype: 'pebble', color: '#12a594',
      title: 'Invalidate the CloudFront cache',
      detail: 'production · immediate and irreversible · paths /*' },
    { id: 'run-1', kind: 'run', agent: 'Ansel', archetype: 'paper', color: '#2b6bff',
      title: 'Console build synced to S3', detail: '14 objects · $0.128' },
  ];
}

export function fixtureRooms() {
  return [
    { id: 'rm-launch', name: 'Launch room', members: ['eng', 'ops', 'cos'],
      last: 'Ansel handed the rollout watch to Pell.' },
    { id: 'rm-week', name: 'Monday planning', members: ['cos', 'res'],
      last: 'Wren drafted the week from the open threads.' },
  ];
}

export function fixtureRoutines() {
  return [
    { id: 'rt-brief', name: 'Morning brief', cadence: 'Weekdays · 07:30',
      agent: 'cos', state: 'idle', next: 'Tomorrow 07:30' },
    { id: 'rt-alarms', name: 'Overnight alarm sweep', cadence: 'Daily · 02:00',
      agent: 'ops', state: 'idle', next: 'Tonight 02:00' },
  ];
}

export function fixtureArtifacts() {
  return [
    { id: 'ar-1', name: 'rollout-evidence-9a22.zip', kind: 'Evidence bundle',
      agent: 'Ansel', at: '2 hours ago', sealed: true },
    { id: 'ar-2', name: '5xx-investigation.md', kind: 'Report',
      agent: 'Pell', at: 'Yesterday', sealed: true },
  ];
}
