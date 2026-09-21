import { useEffect, useState } from 'react';
import { stepLabel } from '../lib/tools';
import Icon from './Icon';

function span(seconds) {
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, '0')}s`;
}

// What each verdict is called, and in which words the rule behind it is said. The
// control plane names the rule (`policy.Decision.rule`); this is only how the
// console phrases it -- it never re-decides or upgrades a verdict.
const VERDICT = { allowed: 'Allowed', asked: 'Asked', denied: 'Denied' };

export function ruleSentence(review) {
  if (!review) return '';
  switch (review.rule) {
    case 'floor': return `on the always-approve floor (${review.matched})`;
    case 'capability': return `${review.matched} actions always need a decision`;
    case 'default': return 'a write with no pre-approved rule';
    case 'read': return 'read-only';
    case 'preapproved': return 'covered by a pre-approved rule';
    case 'approved': return 'you approved these exact arguments';
    case 'sandbox': return 'ran in its own sandbox';
    case 'no_grant': return 'not granted to this Bot';
    case 'never': return `never approvable (${review.matched})`;
    default: return review.reason || '';
  }
}

/** Allowed / Asked / Denied, with the reason one hover or one line away. Colour
 *  is never the only carrier: the word is always drawn. */
export function Verdict({ review }) {
  if (!review) return null;
  return (
    <span className={`verdict verdict--${review.decision}`} title={review.reason}>
      <i aria-hidden="true" />{VERDICT[review.decision] || review.decision}
    </span>
  );
}

/**
 * What a run did, folded to one line.
 *
 * The transcript is for what was said. A run's tool calls are how it got
 * there, and five of them stacked between two sentences bury both. So they
 * collapse into "Worked for 14s · 5 steps", open while the run is live (that is
 * the moment someone is watching) and shut once it ends -- the memo's third
 * presence layer, "how much do I need to know", answered by one line first and
 * the trail only on demand.
 *
 * Each step carries Auto Review's verdict and the rule behind it, and the header
 * says up front if anything was asked or denied: the steps that needed a person
 * are the ones worth seeing without opening it. A step's tool gets a plain-word
 * label (`shell` reads "Terminal") with the exact identifier on hover, and its
 * summary -- what was actually run or done -- is shown untouched: a paraphrase of
 * what an agent did is not evidence of it.
 */
export default function StepsGroup({ steps }) {
  const running = !steps.endedAt;
  const [open, setOpen] = useState(running);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!running) return undefined;
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(tick);
  }, [running]);

  // Closed when the run ends. A reader who opened it stays in charge until
  // then; after that it is history.
  useEffect(() => { if (!running) setOpen(false); }, [running]);

  const seconds = Math.max(1, Math.round(((steps.endedAt || now) - steps.startedAt) / 1000));
  const count = steps.items.length;
  const asked = steps.items.filter((s) => s.review?.decision === 'asked').length;
  const denied = steps.items.filter((s) => s.review?.decision === 'denied').length;
  const flags = [asked && `${asked} asked`, denied && `${denied} denied`].filter(Boolean).join(' · ');
  const label = running
    ? `Working · ${span(seconds)}`
    : `Worked for ${span(seconds)} · ${count} step${count === 1 ? '' : 's'}${flags ? ` · ${flags}` : ''}`;

  return (
    <div className={`steps${running ? ' is-running' : ''}${denied ? ' has-denied' : asked ? ' has-asked' : ''}`}>
      <button type="button" className="steps-head" aria-expanded={open}
              onClick={() => setOpen((o) => !o)}>
        <span className="steps-mark" aria-hidden="true">
          {running ? <i className="steps-spin" /> : <Icon name="check" size={14} className="steps-check" />}
        </span>
        <span>{label}</span>
      </button>
      {open && (
        <ol className="steps-list">
          {steps.items.map((step, i) => (
            <li key={i} className={running && i === count - 1 ? 'is-current' : ''}>
              <span className="step-line">
                <span className="step-name" title={step.name}>{stepLabel(step.name)}</span>
                <Verdict review={step.review} />
              </span>
              <span>{step.summary}</span>
              {step.review && <em className="step-why">{ruleSentence(step.review)}</em>}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
