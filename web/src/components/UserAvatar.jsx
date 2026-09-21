import { useAuth0 } from '../auth0';

/** The person, as a circle: their picture if they have one, else an initial. */
export default function UserAvatar({ size = 40, className = '' }) {
  const { user } = useAuth0();
  const initial = (user?.name || user?.email || '?').trim().charAt(0).toUpperCase();
  return user?.picture
    ? <img className={`ua ${className}`} src={user.picture} alt="" width={size} height={size} referrerPolicy="no-referrer" />
    : <span className={`ua ua-initial ${className}`} style={{ width: size, height: size, fontSize: size * 0.42 }} aria-hidden="true">{initial}</span>;
}
