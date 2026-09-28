import { ARCHETYPES } from './archetypes';

/**
 * A companion, in one of seven states.
 *
 * Every state is announced three ways: an animation, a label, and a colour.
 * The animation is the least reliable of the three — it is invisible under
 * `prefers-reduced-motion`, on a slow frame, or to someone reading the DOM —
 * so it is never the only carrier. `statusText` is always in the tree.
 *
 * Motion is CSS on SVG nodes: transforms and opacity only, which the
 * compositor handles without repainting. No canvas, no rAF loop, no
 * per-frame React. A list of twenty of these costs the same as one.
 */

// The `verb` is the plain, present-tense phrase an operator reads on hover and
// in the desk "Now" pane. It is the coarse fallback for a state; a live run
// supplies a finer action line (typing, using a tool, creating an artifact,
// sending a message) through `presence.js`, and where one exists it is shown
// in place of the verb. These phrasings are the canonical activity vocabulary,
// kept word-for-word in step with `presence.js` RUN_STATES and
// `services/amazai/presence.py` so the avatar, the hover line and the durable
// GET /presence read never describe the same agent three different ways.
export const STATES = {
  idle:     { label: 'Idle',            tone: 'neutral', verb: 'resting' },
  thinking: { label: 'Thinking',        tone: 'accent',  verb: 'thinking it through' },
  working:  { label: 'Working',         tone: 'accent',  verb: 'working on it' },
  // Paused on something outside itself -- a connector, another Bot -- rather
  // than on you. Quieter than `approval` on purpose: nothing is being asked of
  // the operator, so nothing about it should look like a request.
  waiting:  { label: 'Waiting',         tone: 'neutral', verb: 'waiting on a teammate' },
  approval: { label: 'Needs you',       tone: 'warn',    verb: 'waiting for your approval' },
  complete: { label: 'Done',            tone: 'ok',      verb: 'done' },
  blocked:  { label: 'Blocked',         tone: 'danger',  verb: 'blocked' },
  offline:  { label: 'Off',             tone: 'muted',   verb: 'switched off' },
};

export const STATE_KEYS = Object.keys(STATES);

/** Darken an accent for interior contrast. Kept here so archetypes take a
 *  plain pair of colours and never reason about theming. */
function deepen(hex) {
  const n = parseInt(hex.replace('#', ''), 16);
  const f = 0.68;
  const r = Math.round(((n >> 16) & 255) * f);
  const g = Math.round(((n >> 8) & 255) * f);
  const b = Math.round((n & 255) * f);
  return `#${((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1)}`;
}

export default function Companion({
  archetype = 'pebble',
  color = '#2b6bff',
  state = 'idle',
  size = 48,
  name = '',
  showLabel = false,
  className = '',
  // Beside its own name, an avatar's spoken description says nothing new -- and its
  // hidden text lands in the clipboard when someone copies a conversation.
  decorative = false,
}) {
  const arch = ARCHETYPES[archetype] || ARCHETYPES.pebble;
  const { Shape } = arch;
  const meta = STATES[state] || STATES.idle;
  const deep = deepen(color);
  const described = name ? `${name} — ${meta.label}, ${meta.verb}` : `${meta.label}, ${meta.verb}`;

  return (
    <span className={`cc cc-${state} ${className}`} data-state={state}>
      <svg className="cc-svg" width={size} height={size} viewBox="0 0 100 100"
           {...(decorative ? { 'aria-hidden': 'true', focusable: 'false' } : { role: 'img', 'aria-label': described })}>
        {!decorative && <title>{described}</title>}

        <g className="cc-stage">
          <Shape c={color} deep={deep} />
        </g>

        {/* --- per-state ornaments ------------------------------------- */}

        {/* Thinking: three points find each other and connect. */}
        {state === 'thinking' && (
          <g className="cc-constellation" stroke={color} fill={color}>
            <line className="cc-link" x1="24" y1="26" x2="50" y2="14" strokeWidth="1.6" />
            <line className="cc-link cc-link-2" x1="50" y1="14" x2="78" y2="28" strokeWidth="1.6" />
            <circle className="cc-star" cx="24" cy="26" r="3" />
            <circle className="cc-star cc-star-2" cx="50" cy="14" r="3.4" />
            <circle className="cc-star cc-star-3" cx="78" cy="28" r="3" />
          </g>
        )}

        {/* Working: task chips orbit the companion and get sorted. */}
        {state === 'working' && (
          <g className="cc-orbit">
            <rect className="cc-chip" x="44" y="2" width="13" height="9" rx="2.5" fill={color} />
            <rect className="cc-chip cc-chip-2" x="44" y="2" width="13" height="9" rx="2.5"
                  fill={color} opacity=".75" />
            <rect className="cc-chip cc-chip-3" x="44" y="2" width="13" height="9" rx="2.5"
                  fill={color} opacity=".5" />
          </g>
        )}

        {/* Waiting: three dots take turns, and it does not hurry them. */}
        {state === 'waiting' && (
          <g className="cc-wait" fill={color}>
            <circle className="cc-wait-a" cx="60" cy="14" r="3.2" />
            <circle className="cc-wait-b" cx="72" cy="14" r="3.2" />
            <circle className="cc-wait-c" cx="84" cy="14" r="3.2" />
          </g>
        )}

        {/* Approval: the companion holds up a small token and waits. */}
        {state === 'approval' && (
          <g className="cc-token">
            <rect x="60" y="8" width="30" height="22" rx="5"
                  fill="var(--warn)" opacity=".18" />
            <rect x="60" y="8" width="30" height="22" rx="5"
                  fill="none" stroke="var(--warn)" strokeWidth="2.5" />
            <path d="M67 19h16M67 25h10" stroke="var(--warn)" strokeWidth="2.5"
                  strokeLinecap="round" />
          </g>
        )}

        {/* Complete: one small star opens and settles. */}
        {state === 'complete' && (
          <g className="cc-flourish">
            <path className="cc-pop"
                  d="M76 14l3.4 7.6L87 25l-7.6 3.4L76 36l-3.4-7.6L65 25l7.6-3.4Z"
                  fill="var(--ok)" />
            <circle className="cc-ring" cx="50" cy="50" r="40" fill="none"
                    stroke="var(--ok)" strokeWidth="2" />
          </g>
        )}

        {/* Blocked: a tangle, not an alarm. */}
        {state === 'blocked' && (
          <path className="cc-tangle"
                d="M32 22c10 6 4 14 12 16s14-8 20-2-6 12 2 16"
                fill="none" stroke="var(--danger)" strokeWidth="3"
                strokeLinecap="round" opacity=".85" />
        )}
      </svg>

      {/* Never animation alone. */}
      {!decorative && <span className="cc-sr">{described}</span>}
      {showLabel && (
        <span className={`cc-label cc-tone-${meta.tone}`}>
          <i className="cc-dot" aria-hidden="true" />
          {meta.label}
        </span>
      )}
    </span>
  );
}

/** The compact form for list rows, participant strips and timeline entries. */
export function CompanionChip({ archetype, color, state, name, size = 20 }) {
  const meta = STATES[state] || STATES.idle;
  return (
    <span className="cc-chip-inline" title={`${name || 'Agent'} — ${meta.label}`}>
      <Companion archetype={archetype} color={color} state={state}
                 size={size} name={name} />
      <i className={`cc-pip cc-tone-${meta.tone}`} aria-hidden="true" />
    </span>
  );
}
