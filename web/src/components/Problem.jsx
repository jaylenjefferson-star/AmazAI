import { friendly, retryLabel } from '../lib/errors';

/**
 * A failure, kept compact and in place: what happened in one calm sentence and
 * a way forward. Never the raw error, never a wall.
 */
export default function Problem({ error, message, fallback, onRetry, inline = false }) {
  const text = message || friendly(error, fallback);
  return (
    <div className={`problem${inline ? ' problem--inline' : ''}`} role="alert">
      <span>{text}</span>
      {onRetry && <button type="button" className="problem-retry" onClick={onRetry}>{retryLabel(error)}</button>}
    </div>
  );
}
