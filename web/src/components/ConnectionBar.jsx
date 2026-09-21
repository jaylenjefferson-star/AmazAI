import { useConnection } from '../presence';

/**
 * Says so when the console is not hearing anything.
 *
 * `ws.js` has always computed this -- 'connected', 'disconnected',
 * 'reconnecting in Ns' -- and nothing read it. Without it a dead socket is
 * indistinguishable from a Bot with nothing to say: the animations freeze, the
 * conversation stops updating, and there is no way to tell whether the work
 * stopped or only the reporting of it did.
 *
 * Quiet by default. Nothing is drawn while connected, nothing on the first
 * connect (a bar that flashes on every load teaches people to ignore it), and
 * nothing when no socket is configured at all -- there is no reconnection
 * pending in that build, so claiming one would be a promise with no end.
 */
export default function ConnectionBar() {
  const { status } = useConnection();
  const down = status === 'disconnected' || status.startsWith('reconnecting');
  if (!down) return null;

  const retry = status.startsWith('reconnecting') ? status.replace('reconnecting', 'Retrying') : null;

  return (
    <div className="conn" role="status" aria-live="polite">
      <i aria-hidden="true" />
      <span>
        Not connected. Live updates are paused, so what a Bot is doing right now
        may be out of date.
      </span>
      {retry && <em>{retry}</em>}
    </div>
  );
}
