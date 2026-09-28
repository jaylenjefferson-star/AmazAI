/**
 * "Ansel is working…", with the three dots iMessage made everyone expect.
 *
 * Deliberately reuses the same verb vocabulary as `characters/Companion`'s
 * state chip (`thinking` -> "thinking it through", `working` -> "working on
 * it") rather than inventing a second one, so the avatar and this line never
 * disagree about what the agent is doing.
 */
export default function TypingIndicator({ name, verb = 'is working' }) {
  return (
    <div className="msg enter" aria-live="polite">
      <div className="who">{name}</div>
      <div className="body typing-dots">
        <span>{name} {verb}</span>
        <span className="dots" aria-hidden="true"><i /><i /><i /></span>
      </div>
    </div>
  );
}
