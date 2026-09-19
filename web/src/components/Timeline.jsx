import { useEffect, useRef, useState } from 'react';
import ApprovalCard from './ApprovalCard';

function ToolChip({ chip }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="chip">
      <span className="tool">⚙ {chip.name}</span>
      <div>
        <button onClick={() => setOpen((o) => !o)}>
          {chip.summary || '(no detail)'} {open ? '▾' : '▸'}
        </button>
        {open && <pre>{JSON.stringify(chip, null, 2)}</pre>}
      </div>
    </div>
  );
}

/**
 * Chat and execution timeline are one column, not two. A tool call is a turn
 * in the conversation, because that is what it actually is.
 */
export default function Timeline({ items, streaming, approvals, onDecide }) {
  const endRef = useRef(null);
  const [stuck, setStuck] = useState(true);

  useEffect(() => {
    if (stuck) endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [items, streaming, stuck]);

  function onScroll(e) {
    const el = e.currentTarget;
    setStuck(el.scrollHeight - el.scrollTop - el.clientHeight < 80);
  }

  return (
    <div className="timeline" onScroll={onScroll}>
      {items.length === 0 && !streaming && (
        <div className="empty">Nothing here yet. Describe a task below.</div>
      )}

      {items.map((item, i) => {
        if (item.type === 'tool') return <ToolChip key={i} chip={item} />;
        if (item.type === 'approval') {
          const live = approvals.find((a) => a.approvalId === item.approval.approvalId)
            || item.approval;
          return (
            <ApprovalCard key={i} approval={live}
                          onDecide={(ok, note) => onDecide(live, ok, note)} />
          );
        }
        return (
          <div className="msg" key={i}>
            <div className="who">{item.author || (item.role === 'user' ? 'you' : 'agent')}</div>
            <div className="body">{item.text}</div>
          </div>
        );
      })}

      {streaming != null && (
        <div className="msg">
          <div className="who">{streaming.author || 'agent'}</div>
          <div className="body">{streaming.text}<span className="cursor" /></div>
        </div>
      )}

      <div ref={endRef} />
    </div>
  );
}
