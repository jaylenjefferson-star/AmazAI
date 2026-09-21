/** Placeholder rows while a list loads: the shape of the thing, not a spinner. */
export function RosterSkeleton({ rows = 7 }) {
  return (
    <div className="skel-list" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <div className="skel-row" key={i} style={{ '--i': i }}>
          <span className="skel skel-avatar" />
          <span className="skel-lines">
            <span className="skel skel-line skel-name" style={{ width: `${44 + ((i * 17) % 30)}%` }} />
            <span className="skel skel-line skel-preview" style={{ width: `${62 + ((i * 11) % 28)}%` }} />
          </span>
        </div>
      ))}
    </div>
  );
}

export function ChatSkeleton() {
  return (
    <div className="skel-chat" aria-busy="true" aria-label="Loading conversation">
      <span className="skel skel-bubble skel-l" />
      <span className="skel skel-bubble skel-r" />
      <span className="skel skel-bubble skel-l skel-tall" />
    </div>
  );
}
