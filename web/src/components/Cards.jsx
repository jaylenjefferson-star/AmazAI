import ToolsSheet from './ToolsSheet';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { SCHEDULE_PRESETS } from '../schedules';
import Icon from './Icon';

/**
 * The shapes a Bot's message can take besides prose.
 *
 * The transcript is heterogeneous on purpose: when the honest answer to "I need
 * your inbox" is a button, a paragraph asking you to go and find the button is
 * a worse answer. But every card here is one of two things -- a **proposal**
 * the operator acts on, or an **artifact** they can open -- and none of them is
 * authority. A routine card does not create a routine; it opens the routine
 * form filled in, and the operator submits it. A Bot card opens the create
 * form. That is the whole rule: agents propose, and the console's own gates
 * (`agents do not create agents; a person does`) decide.
 *
 * Cards arrive as `message.cards[]`. Which server code produces them is
 * gated on decision D4 (see CLAUDE.md); this renders whatever is sent.
 */

function Frame({ icon, title, children, actions }) {
  return (
    <div className="card">
      <div className="card-main">
        <span className="card-icon" aria-hidden="true">{icon}</span>
        <div className="card-text">
          <strong>{title}</strong>
          {children}
        </div>
      </div>
      {actions && <div className="card-actions">{actions}</div>}
    </div>
  );
}

/** A tool the Bot needs and the account has not connected. "Not now" is a real
 *  answer: the Bot carries on with what it can do without it. */
function ConnectCard({ card }) {
  const [dismissed, setDismissed] = useState(false);
  const [picking, setPicking] = useState(false);
  if (dismissed) return null;
  return (
    <Frame icon={<Icon name="plug" size={20} />} title={`Connect ${card.name}`}
           actions={(
             <>
               <button type="button" className="primary" onClick={() => setPicking(true)}>Connect {card.name}</button>
               <button type="button" className="ghost" onClick={() => setDismissed(true)}>Not now</button>
             </>
           )}>
      <span>{card.why}</span>
      {picking && <ToolsSheet initialQuery={card.name} onClose={() => setPicking(false)} />}
    </Frame>
  );
}

/** Something a run produced. Opens the Artifacts screen, where sealed output
 *  lives -- a file card that opened nothing would be a picture of a button. */
function FileCard({ card }) {
  return (
    <Link className="card card--link" to="/artifacts">
      <div className="card-main">
        <span className="card-icon" aria-hidden="true"><Icon name="file" size={20} /></span>
        <div className="card-text">
          <strong>{card.name}</strong>
          <span>{card.meta || 'View in Artifacts'}</span>
        </div>
      </div>
    </Link>
  );
}

/** "Do this every weekday morning?" -- filled in, not created. Only a preset
 *  can be named (`schedules.SCHEDULE_PRESETS`), never a raw expression. */
function RoutineCard({ card, ctx }) {
  const navigate = useNavigate();
  const preset = SCHEDULE_PRESETS.find((p) => p.key === card.preset && p.key !== 'custom');
  if (!preset) return null;   // a proposal that cannot be honoured is not shown
  return (
    <Frame icon={<Icon name="clock" size={20} />} title={card.name}
           actions={(
             <button type="button" className="primary"
                     onClick={() => navigate('/routines/new', { state: { prefill: {
                       name: card.name, agentId: ctx?.agentId || '',
                       prompt: card.prompt, preset: card.preset,
                     } } })}>
               Set up routine
             </button>
           )}>
      <span>{preset.label}</span>
      <span className="card-note">You will review it before it is created.</span>
    </Frame>
  );
}

const CARDS = { connect: ConnectCard, file: FileCard, routine: RoutineCard };

export default function Card({ card, ctx }) {
  const Kind = CARDS[card?.type];
  // An unknown card is dropped, not drawn as an error: a newer server than this
  // console must not turn a working conversation into a broken one.
  return Kind ? <Kind card={card} ctx={ctx} /> : null;
}

/** A line of system history in the transcript: a routine created, a handoff
 *  accepted. Quiet, and centred, because it is something that happened rather
 *  than something said. */
export function EventLine({ event }) {
  return (
    <div className="event-line">
      <Icon name={event.icon || 'check'} size={14} />
      <span>{event.text}</span>
    </div>
  );
}
