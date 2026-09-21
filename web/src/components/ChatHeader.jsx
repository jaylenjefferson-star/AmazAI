import { Link } from 'react-router-dom';
import Icon from './Icon';

/**
 * The header floats: a back control, a pill with who you are talking to, and one
 * action. There is no bar spanning the screen, so the conversation appears to
 * exist behind and around these three controls.
 *
 * The pill is the way to the agent's profile (its identity, tools, permissions),
 * so nothing about the agent needs a menu of its own. The action on the right is
 * the agent's workspace -- its desk.
 */
export default function ChatHeader({ back = '/', mark, name, status = '', tone = '', onOpen, action }) {
  return (
    <header className="ch">
      <Link to={back} className="ch-round" aria-label="Back">
        <Icon name="back" size={22} />
      </Link>

      <button type="button" className="ch-pill" onClick={onOpen} aria-label={`About ${name}`}>
        <span className="ch-mark">{mark}</span>
        <span className="ch-text">
          <strong>{name}</strong>
          {status && <small className={tone ? `tone-${tone}` : undefined}>{status}</small>}
        </span>
      </button>

      {action ? (
        <button type="button" className="ch-round" aria-label={action.label} onClick={action.onClick}>
          <Icon name={action.icon} size={21} />
          {action.badge && <i className="ch-badge" aria-hidden="true" />}
        </button>
      ) : <span className="ch-round ch-round--ghost" />}
    </header>
  );
}
