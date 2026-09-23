import { Children, cloneElement, isValidElement, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import ApprovalCard from './ApprovalCard';
import Icon from './Icon';
import Card, { EventLine } from './Cards';
import Companion from '../characters/Companion';
import Delegation from './Delegation';
import { useContacts } from './ContactCard';
import StepsGroup from './StepsGroup';
import TypingIndicator from './TypingIndicator';
import { stepLabel } from '../lib/tools';

function ToolChip({ chip }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="chip enter">
      {/* Plain words, with the exact identifier on hover -- the same rule the
          steps trail follows. This drew `group_chat.create` before. */}
      <span className="tool" title={chip.name}>{stepLabel(chip.name)}</span>
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
function Mention({ value, ids, onContact }) {
  const id = value.slice(1);
  if (!ids?.includes(id)) return value;
  // A mention is a way to a contact card, not just a coloured word.
  return onContact
    ? <button type="button" className="mention" aria-label={`Open ${id} contact`}
              onClick={() => onContact(id)}>{value}</button>
    : <span className="mention">{value}</span>;
}

function looksLikeEmailAt(text, mentionStart) {
  let left = mentionStart;
  let right = mentionStart;
  while (left > 0 && !/\s/.test(text[left - 1])) left -= 1;
  while (right < text.length && !/\s/.test(text[right])) right += 1;
  const token = text.slice(left, right);
  const at = mentionStart - left;
  const local = token.slice(0, at).replace(/^[([{<]+/, '');
  const domain = token.slice(at + 1).replace(/[),.;:!?\]}>]+$/, '');
  const quoted = /^"[^"\r\n]+"$/u.test(local);
  const unquoted = /^[\p{L}\p{N}!#$%&'*+/=?^_`{|}~.-]+$/u.test(local)
    && !/[.,]$/.test(local);
  const dottedDomain = /^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$/.test(domain);
  return !!local && (quoted || unquoted) && dottedDomain;
}

function mentionText(value, ids, onContact, keyPrefix) {
  const text = String(value);
  const out = [];
  const pattern = /(^|[^\w@])@([\w-]+)/g;
  let cursor = 0;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    const start = match.index + match[1].length;
    const valueAt = `@${match[2]}`;
    if (looksLikeEmailAt(text, start)) continue;
    if (start > cursor) out.push(text.slice(cursor, start));
    out.push(<Mention key={`${keyPrefix}:${start}`} value={valueAt}
                      ids={ids} onContact={onContact} />);
    cursor = start + valueAt.length;
  }
  if (cursor < text.length) out.push(text.slice(cursor));
  return out.length ? out : text;
}

/** Add contact actions only to ordinary prose. A mention inside a link or code
 * stays text: nesting a button inside an anchor is invalid, and source/code
 * examples should never become actions. */
function withMentions(children, ids, onContact, keyPrefix = 'm') {
  return Children.map(children, (child, index) => {
    if (typeof child === 'string') {
      return mentionText(child, ids, onContact, `${keyPrefix}:${index}`);
    }
    if (!isValidElement(child) || child.props?.children == null) return child;
    // react-markdown represents overridden components (notably our safe link)
    // as function elements before they render. Each overridden block decorates
    // its own children; descending here would turn a link label into a button
    // before the link component has a chance to keep it as text.
    if (typeof child.type !== 'string' || child.type === Mention) return child;
    const tag = child.type;
    if (['a', 'code', 'pre'].includes(tag)) return child;
    return cloneElement(child, undefined,
      withMentions(child.props.children, ids, onContact, `${keyPrefix}:${index}`));
  });
}

const MARKDOWN_ELEMENTS = [
  'p', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'ul', 'ol', 'li', 'blockquote', 'hr', 'strong', 'em', 'code', 'pre', 'a',
  // GFM, via remarkGfm below: tables (a model reaches for these constantly
  // for anything comparative -- a roster, a before/after, a recommendation
  // list) and struck-through text.
  'table', 'thead', 'tbody', 'tr', 'th', 'td', 'del',
];

// A stable reference, not a fresh array literal per render: react-markdown
// re-parses when this prop changes identity.
const REMARK_PLUGINS = [remarkGfm];

function safeUrl(value) {
  const url = String(value || '').trim();
  if (/^(https?:|mailto:)/i.test(url)) return url;
  if (/^\/(?!\/)/.test(url) || /^(#|\.\.?\/)/.test(url)) return url;
  return '';
}

/** Safe CommonMark for model chat. Raw HTML and media are excluded; headings
 * are clamped below the page title; mentions remain contact actions outside
 * links/code. The same renderer is used for committed and streaming words, so
 * syntax does not visibly switch meanings when a run finishes. */
export function Body({ text, ids, onContact }) {
  const components = useMemo(() => {
    const decorate = (children, key) => withMentions(children, ids, onContact, key);
    const block = (Tag, key) => function MarkdownBlock({ node: _node, children, ...props }) {
      return <Tag {...props}>{decorate(children, key)}</Tag>;
    };
    return {
      p: block('p', 'p'),
      li: block('li', 'li'),
      blockquote: block('blockquote', 'quote'),
      h1: block('h3', 'h1'), h2: block('h3', 'h2'), h3: block('h3', 'h3'),
      h4: block('h4', 'h4'), h5: block('h4', 'h5'), h6: block('h4', 'h6'),
      // A table scrolls sideways on its own (see .body--markdown table in
      // styles.css) rather than forcing the whole bubble wider than the
      // column -- the same reason `pre` does.
      table({ node: _node, children, ...props }) {
        return <div className="md-table-wrap"><table {...props}>{children}</table></div>;
      },
      th: block('th', 'th'),
      td: block('td', 'td'),
      a({ node: _node, href, children, ...props }) {
        const safe = safeUrl(href);
        if (!safe) return <span>{children}</span>;
        const external = /^https?:/i.test(safe);
        return <a {...props} href={safe} target={external ? '_blank' : undefined}
                  rel={external ? 'noopener noreferrer' : undefined}>{children}</a>;
      },
    };
  }, [ids, onContact]);
  if (!text) return null;
  return (
    <ReactMarkdown skipHtml unwrapDisallowed allowedElements={MARKDOWN_ELEMENTS}
                   remarkPlugins={REMARK_PLUGINS}
                   urlTransform={safeUrl} components={components}>
      {String(text)}
    </ReactMarkdown>
  );
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
  const listRef = useRef(null);
  const { open: openContact } = useContacts();
  const [stuck, setStuck] = useState(true);
  // Options are an offer to answer the newest thing said. Once anything follows
  // -- a reply, a run -- they are stale, so only the last message carries them.
  const lastMessage = items.reduce((at, item, i) => (item.type === 'message' ? i : at), -1);

  // A row is the same row however many rows come before it: keyed by what it *is*,
  // not by where it sits. An index key made every row after an insertion a new node
  // (and re-played its entrance), which read as the whole conversation flipping.
  const keys = items.map((item, i) => item.key ?? `${item.type}:${i}`);
  // Only rows that were not here a moment ago animate in. What arrives with the first
  // load (a history) is not "new", and neither is a row that merely moved.
  const seen = useRef(null);
  const fresh = new Set();
  if (seen.current) for (const k of keys) if (!seen.current.has(k)) fresh.add(k);
  useEffect(() => {
    if (!seen.current) { if (items.length) seen.current = new Set(keys); } else keys.forEach((k) => seen.current.add(k));
  });

  // Follow the conversation only while you are at the bottom of it, instantly, and only
  // by moving *this* list. scrollIntoView moves every scrollable ancestor as well, and
  // an animated scroll re-started on every update is what made it feel like it swayed.
  const justSent = [...fresh].some((k) => String(k).startsWith('local:'));
  useLayoutEffect(() => {
    const el = listRef.current;
    if (!el) return;
    // What you have just sent always brings you to it, even from up in the history; what
    // arrives from someone else follows only if you were already at the bottom.
    if (justSent && !stuck) setStuck(true);
    // 'instant' overrides any inherited scroll-behavior: an animated scroll started on every
    // update was what made the list sway (and could be cancelled half-way, stranding it).
    if (stuck || justSent) el.scrollTo({ top: el.scrollHeight, behavior: 'instant' });
  }, [items, streaming, typing]);  // eslint-disable-line react-hooks/exhaustive-deps

  function onScroll(e) {
    const el = e.currentTarget;
    setStuck(el.scrollHeight - el.scrollTop - el.clientHeight < 80);
  }

  return (
    <div className="timeline" ref={listRef} onScroll={onScroll}
         role="log" aria-label="Conversation" aria-live="polite"
         aria-busy={streaming != null ? 'true' : undefined}>
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
                            onDecide={(ok, note, opts) => onDecide(live, ok, note, opts)} />
            </>
          );
        } else {
          const mine = item.role === 'user';
          const speaker = showAuthor ? speakerFor(items, i, agents) : null;
          const offer = i === lastMessage && !streaming && !typing
            && !mine && item.suggestions?.length && onSuggest;
          node = (
            <div className={`msg ${mine ? 'user' : ''}`}>
              {speaker && (
                <button type="button" className="tl-label tl-label--who" disabled={!speaker.who}
                        onClick={() => speaker.who && openContact(speaker.who.agentId)}>
                  {speaker.who && <Companion archetype={speaker.who.archetype} color={speaker.who.color} state="idle" size={18} decorative />}
                  {speaker.name}
                </button>
              )}
              {item.text && <div className="body body--markdown"><Body text={item.text} ids={mentionIds} onContact={openContact} /></div>}
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
          <div key={keys[i]} className={`tl-row${fresh.has(keys[i]) ? ' is-new' : ''}`}>
            {sep && <div className="time-sep">{sep}</div>}
            {node}
          </div>
        );
      })}

      {streaming != null && (
        <div className="msg">
          {showAuthor && <div className="who">{streaming.author || 'agent'}</div>}
          <div className="body body--markdown body--streaming">
            <Body text={streaming.text} ids={mentionIds} onContact={openContact} />
            <span className="cursor" aria-hidden="true" />
          </div>
        </div>
      )}

      {streaming == null && typing != null && (
        <TypingIndicator name={typing.name} verb={typing.verb} />
      )}

    </div>
  );
}
