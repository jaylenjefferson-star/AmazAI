/**
 * What a tool is called to a person.
 *
 * The control plane names tools the way code does -- `shell`, `file_operations`,
 * `request_connector`, `GMAIL_SEND_EMAIL`. That is the right identifier and it is
 * kept: a step still carries the exact name (in its tooltip and its accessible
 * label). It is just not the first thing a person reads. This only *labels*; it
 * never decides anything, and a name it does not know is shown as it is rather
 * than guessed at.
 */

/** The tools a Bot has built in. */
export const BUILT_IN = {
  shell: 'Terminal',
  file_operations: 'Files',
  browser: 'Browser',
  code_interpreter: 'Code',
};

const STEP = {
  ...BUILT_IN,
  connector_search: 'Looked through your apps',
  connector_call: 'Used an app',
  request_connector: 'Asked to connect an app',
  request_approval: 'Asked for approval',
  propose_routine: 'Suggested a routine',
  create_agent: 'Created a Bot',
  update_agent: 'Refined a Bot',
  propose_agent: 'Suggested a new Bot',
  'agent.create': 'Suggested a new Bot',        // waiting on your approval
  'agent.created': 'Created a Bot',
  'agent.update': 'Refined a Bot',
  propose_skill: 'Suggested a skill',
  'skill.create': 'Suggested a skill',
  propose_shared_memory: 'Suggested a shared memory',
  'memory.publish': 'Suggested a shared memory',
  remember: 'Saved to memory',
  message_agent: 'Messaged a teammate',
  handoff: 'Handed off',
};

// A Composio tool slug: the app, then what it does, in capitals with underscores.
const COMPOSIO_SLUG = /^([A-Z][A-Z0-9]*)_([A-Z0-9]+(?:_[A-Z0-9]+)*)$/;

const sentence = (s) => s.charAt(0).toUpperCase() + s.slice(1);

/** A short, plain label for one step's tool. Unknown names come back unchanged. */
export function stepLabel(name) {
  if (!name) return '';
  if (STEP[name]) return STEP[name];
  const slug = COMPOSIO_SLUG.exec(name);
  if (slug) {
    const app = sentence(slug[1].toLowerCase());
    return `${app}: ${slug[2].toLowerCase().replace(/_/g, ' ')}`;
  }
  return name;
}
