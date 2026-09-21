import { Fragment, useEffect, useRef, useState } from 'react';
import ApprovalCard from './ApprovalCard';
import Icon from './Icon';
import Card, { EventLine } from './Cards';
import Companion from '../characters/Companion';
import Delegation from './Delegation';
import { useContacts } from './ContactCard';
import StepsGroup from './StepsGroup';
import TypingIndicator from './TypingIndicator';

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

// A new time stamp after this long a gap, as every messaging app does: one per
// burst of conversation, not one per message.
const GAP_MS = 30 * 60_000;

function stamp(at) {
  const when = new Date(at);
  const time = when.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  return when.toDateString() === new Date().toDateString()
    ? time
    : `${when.toLocaleDateString([], { month: 'short', day: 'numeric' })}, ${time}`;
}

/** The label to draw above `items[i]`, if it starts a new burst. */
function separatorFor(items, i) {
  const at = items[i].at;
  if (items[i].type !== 'message' || !at) return null;
  const prev = items.slice(0, i).reverse().find((x) => x.type === 'message' && x.at);
  if (!prev) return stamp(at);
  return new Date(at) - new Date(prev.at) > GAP_MS ? stamp(at) : null;
}

/** One agent messaging another: not addressed to you, but visible, because the
 *  work between agents is part of what is happening in the room. */
function AgentNote({ note, agents, onContact }) {
  const of = (id) => agents?.find((a) => a.agentId === id);
  const from = of(note.fromAgentId);
  const to = of(note.toAgentId);
  return (
    <div className="tl-note">
      <span className="tl-note-who">
        <button type="button" onClick={() => from && onContact(from.agentId)}>{from?.name || note.fromAgentId}</button>
        <Icon name="arrowright" size={13} />
        <button type="button" onClick={() => to && onContact(to.agentId)}>{to?.name || note.toAgentId}</button>
      </span>
      <span className="tl-note-text">{note.summary}</span>
    </div>
  );
}

/** In a room, a small label above the first message of each agent's burst. Not an
 *  identity card on every bubble: just enough to know who is speaking. */
function speakerFor(items, i, agents) {
  const it = items[i];
  if (it.type !== 'message' || it.role === 'user' || !it.author) return null;
  const prev = items[i - 1];
  if (prev && prev.type === 'message' && prev.role !== 'user' && prev.author === it.author) return null;
  const who = agents?.find((a) => a.agentId === it.author || a.name === it.author);
  return { name: who?.name || it.author, who };
}

/** A message's words, with `@bot` drawn as a mention -- but only for Bots that
 *  exist, so a stray `@word` is never dressed up as an address. */
function Body({ text, ids, onContact }) {
  if (!ids?.length || !text || !text.includes('@')) return text;
  return String(text).split(/(@[\w-]+)/g).map((part, i) => {
    if (!(part.startsWith('@') && ids.includes(part.slice(1)))) return part;
    // A mention is a way to a contact card, not just a coloured word.
    return onContact
      ? <button type="button" key={i} className="mention" onClick={() => onContact(part.slice(1))}>{part}</button>
      : <span key={i} className="mention">{part}</span>;
  });
}

/**
 * Chat and execution timeline are one column, not two. A tool call is a turn
 * in the conversation, because that is what it actually is.
 *
 * `showAuthor` is off in a one-to-one thread -- the name is already in the
 * header, and a label over every bubble says the same thing forty times -- and
 * on in a room, where who spoke is the point.
 */
export default function Timeline({
  items, streaming, typing, approvals, agents, onDecide, onSuggest,
  cardCtx, showAuthor = true, onSaveSkill, onRemember, mentionIds,
}) {
  const endRef = useRef(null);
  const { open: openContact } = useContacts();
  const [stuck, setStuck] = useState(true);
  // Options are an offer to answer the newest thing said. Once anything follows
  // -- a reply, a run -- they are stale, so only the last message carries them.
  const lastMessage = items.reduce((at, item, i) => (item.type === 'message' ? i : at), -1);

  useEffect(() => {
    if (stuck) endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [items, streaming, typing, stuck]);

  function onScroll(e) {
    const el = e.currentTarget;
    setStuck(el.scrollHeight - el.scrollTop - el.clientHeight < 80);
  }

  return (
    <div className="timeline" onScroll={onScroll}>
      {items.length === 0 && !streaming && !typing && (
        <div className="tl-empty">
          <strong>Say hello</strong>
          <span>Describe a job below. You will be asked before anything risky runs.</span>
        </div>
      )}

      {items.map((item, i) => {
        const sep = separatorFor(items, i);
        let node;

        if (item.type === 'tool') {
          node = <ToolChip chip={item} />;
        } else if (item.type === 'steps') {
          node = <StepsGroup steps={item.steps} />;
        } else if (item.type === 'event') {
          node = <EventLine event={item} />;
        } else if (item.type === 'agentnote') {
          node = <AgentNote note={item.note} agents={agents} onContact={openContact} />;
        } else if (item.type === 'handoff') {
          node = <Delegation handoff={item.handoff} agents={agents} />;
        } else if (item.type === 'approval') {
          const live = approvals.find((a) => a.approvalId === item.approval.approvalId)
            || item.approval;
          node = (
            <>
              {live.status === 'pending' && <div className="tl-label tl-label--ask"><i aria-hidden="true" />Waiting on you</div>}
              <ApprovalCard approval={live}
                            onDecide={(ok, note) => onDecide(live, ok, note)} />
            </>
          );
        } else {
          const mine = item.role === 'user';
          const speaker = showAuthor ? speakerFor(items, i, agents) : null;
          const offer = i === lastMessage && !streaming && !typing
            && !mine && item.suggestions?.length && onSuggest;
          node = (
            <div className={`msg enter ${mine ? 'user' : ''}`}>
              {speaker && (
                <button type="button" className="tl-label tl-label--who" disabled={!speaker.who}
                        onClick={() => speaker.who && openContact(speaker.who.agentId)}>
                  {speaker.who && <Companion archetype={speaker.who.archetype} color={speaker.who.color} state="idle" size={18} name={speaker.name} />}
                  {speaker.name}
                </button>
              )}
              {item.text && <div className="body"><Body text={item.text} ids={mentionIds} onContact={openContact} /></div>}
              {!mine && item.cards?.length > 0 && (
                <div className="cards">
                  {item.cards.map((card, j) => <Card key={j} card={card} ctx={cardCtx} />)}
                </div>
              )}
              {/* Two things worth doing with a good answer: keep it as a playbook,
                  or keep the fact. Both are real writes (a skill, a memory row) and
                  both leave a line in the conversation. Quiet until you reach
                  for them, and only on a Bot's own finished words. */}
              {!mine && item.text && !item.suggestions?.length && (onSaveSkill || onRemember) && (
                <div className="msg-actions">
                  {onSaveSkill && <button type="button" onClick={() => onSaveSkill(item)}>Save as skill</button>}
                  {onRemember && <button type="button" onClick={() => onRemember(item)}>Remember this</button>}
                </div>
              )}
              {offer && (
                <div className="suggest-row" role="group" aria-label="Suggested replies">
                  {item.suggestions.map((s) => (
                    <button key={s} type="button" className="suggest"
                            onClick={() => onSuggest(s)}>{s}</button>
                  ))}
                </div>
              )}
            </div>
          );
        }

        return (
          <Fragment key={i}>
            {sep && <div className="time-sep">{sep}</div>}
            {node}
          </Fragment>
        );
      })}

      {streaming != null && (
        <div className="msg">
          {showAuthor && <div className="who">{streaming.author || 'agent'}</div>}
          <div className="body">{streaming.text}<span className="cursor" /></div>
        </div>
      )}

      {streaming == null && typing != null && (
        <TypingIndicator name={typing.name} verb={typing.verb} />
      )}

      <div ref={endRef} />
    </div>
  );
}
