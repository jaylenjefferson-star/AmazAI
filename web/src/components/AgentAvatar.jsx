import Companion from '../characters/Companion';
import { ARCHETYPE_KEYS } from '../characters/archetypes';

/**
 * Adapter from the old avatar call sites onto the companion system.
 *
 * The console used to draw a shape-plus-eyes avatar of its own. That is now
 * one system — `characters/` — and this exists so the screens that predate
 * it render real companions without every call site changing shape at once.
 *
 * Legacy rows carry `shape` values like "squircle" that are not archetypes.
 * Rather than fall back to one default and make every old agent look
 * identical, an unrecognised shape is hashed onto an archetype: stable per
 * agent, and distinct between them.
 */
function archetypeFor(shape, seed = '') {
  if (ARCHETYPE_KEYS.includes(shape)) return shape;
  const key = String(shape || seed || 'a');
  let h = 0;
  for (let i = 0; i < key.length; i += 1) h = (h * 31 + key.charCodeAt(i)) | 0;
  return ARCHETYPE_KEYS[Math.abs(h) % ARCHETYPE_KEYS.length];
}

export default function AgentAvatar({ shape, color = '#2b6bff', size = 24,
                                      name = '', state = 'idle', title }) {
  return (
    <Companion archetype={archetypeFor(shape, name)} color={color}
               state={state} size={size} name={title || name} />
  );
}

export { archetypeFor };
