import { useState } from 'react';
import { Link } from 'react-router-dom';
import PublicShell from '../../components/PublicShell';
import Seo from '../../components/Seo';

const FAQS = [
  {
    q: 'What is an Amaz Credit and how do credits work?',
    a: 'A credit is metered agent work - a model call, a tool use, a routine tick. See the full breakdown on the Pricing page.',
  },
  {
    q: 'How is my data handled?',
    a: 'Session storage is a working cache, discarded after 14 days idle. Anything durable is synced to your own S3 evidence storage, scoped to your account, before a run ends. Read the details in our Privacy Policy.',
  },
  {
    q: 'Do you train models on my data?',
    a: 'No. Your prompts, tool outputs, and evidence records are used to run your agents, not to train any model.',
  },
  {
    q: 'How do agents behave when they are unsure?',
    a: 'An agent that is unsure stops and asks, the same as it does for any action on the always-approve floor. It does not guess on anything irreversible.',
  },
  {
    q: 'What actually requires my approval?',
    a: 'Anything an agent cannot undo - sending, spending, deleting, or granting access - plus anything you have marked as always-approve for a specific agent or connector.',
  },
  {
    q: 'How do connectors work?',
    a: 'AmazAI connects to 1000+ apps securely through Composio, which gives an agent a scoped way to reach a real tool. The third-party credential stays with Composio as an account reference and never enters AmazAI - see the Integrations page for how that is isolated.',
  },
  {
    q: 'Can I delete an agent, and what happens to its history?',
    a: 'Yes, at any time. Its evidence record survives the deletion - the audit trail is append-only and does not depend on the agent still existing.',
  },
  {
    q: 'How do I get help?',
    a: 'Reach out through the Contact page - individual and team plans get email support, Enterprise gets a named contact.',
  },
];

export default function FAQ() {
  const [open, setOpen] = useState(null);
  return (
    <PublicShell>
      <Seo
        title="FAQ"
        description="Answers on Amaz Credits, data handling, model training, agent behavior, approvals, connectors, deletion, and support."
        path="/faq"
      />
      <section className="about-hero">
        <h1>Frequently asked questions</h1>
        <p>Credits, privacy, model and data use, agent behavior, approvals, connectors, deletion, and support.</p>
      </section>

      <section className="faq-list">
        {FAQS.map((item, i) => (
          <div key={item.q} className="faq-item">
            <button
              type="button"
              className="faq-question"
              onClick={() => setOpen(open === i ? null : i)}
              aria-expanded={open === i}
            >
              {item.q}
              <span aria-hidden="true">{open === i ? '-' : '+'}</span>
            </button>
            {open === i && <p className="faq-answer">{item.a}</p>}
          </div>
        ))}
      </section>

      <section className="about-contact">
        <p>Still have a question? <Link to="/contact">Contact us</Link>.</p>
      </section>
    </PublicShell>
  );
}
