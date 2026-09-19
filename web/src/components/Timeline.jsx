import { useEffect, useRef, useState } from 'react';
import ApprovalCard from './ApprovalCard';
import Handoff from './Handoff';

function ToolChip({ chip }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="chip enter">
      <span className="tool">{chip.name}</span>
      <div>
        <button className="disclose" onClick={() => setOpen((o) => !o)}
                aria-expanded={open}>
          <span className="caret" aria-hidden="true">{open ? '▾' : '▸'}</span>
          <span className="summary">{chip.summary || 'no detail'}</span>
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
export default function Timeline({ items, streaming, approvals, agents, onDecide }) {
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
        <div className="empty">
          <span className="title">Nothing here yet</span>
          <span>Describe a task below. You will be asked before anything risky runs.</span>
        </div>
      )}

      {items.map((item, i) => {
        if (item.type === 'tool') return <ToolChip key={i} chip={item} />;
        if (item.type === 'handoff') {
          return <Handoff key={i} handoff={item.handoff} agents={agents} />;
        }
        if (item.type === 'approval') {
          const live = approvals.find((a) => a.approvalId === item.approval.approvalId)
            || item.approval;
          return (
            <ApprovalCard key={i} approval={live}
                          onDecide={(ok, note) => onDecide(live, ok, note)} />
          );
        }
        const mine = item.role === 'user';
        return (
          <div className={`msg enter ${mine ? 'user' : ''}`} key={i}>
            <div className="who">{item.author || (mine ? 'you' : 'agent')}</div>
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
