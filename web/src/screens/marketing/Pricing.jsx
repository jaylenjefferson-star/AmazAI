import { useState } from 'react';
import { Link } from 'react-router-dom';
import PublicShell from '../../components/PublicShell';

const TIERS = [
  { name: 'Explore', price: '$0', period: '/month', credits: '100 Amaz Credits', tagline: 'Build your first AI team', includes: ['Try skills, agents, and safe tasks'] },
  { name: 'Personal', price: '$19', period: '/month', credits: '1,000 Amaz Credits', tagline: 'Your work, delegated', includes: ['Agent workspace', 'Connected tools', 'Approval-first actions'] },
  { name: 'Personal+', price: '$39', period: '/month', credits: '2,500 Amaz Credits', tagline: 'More capacity for daily work', includes: ['More agents', 'More routines', 'Add-on credits'], featured: true },
  { name: 'Pro', price: '$79', period: '/month', credits: '6,000 Amaz Credits', tagline: 'For power users', includes: ['Priority runs', 'Advanced Skills', 'More connected work'] },
  { name: 'Power', price: '$149', period: '/month', credits: '12,000 Amaz Credits', tagline: 'For people who run their work through AmazAI', includes: ['Maximum capacity', 'Advanced controls', 'Priority support'] },
];

const FAQ = [
  { q: 'What is an Amaz Credit?', a: 'One unit of metered agent work — a model call, a tool use, a scheduled routine tick. Read-only checks and approvals themselves never cost credits.' },
  { q: 'What happens if I run out?', a: 'Agents pause new work and tell you what they were about to do. Nothing queues up silently and nothing overspends without your say-so — top up with add-on credits or wait for your next cycle.' },
  { q: 'Do unused credits roll over?', a: 'No — each plan renews with a fresh allotment every billing cycle, which keeps usage predictable for you and for us.' },
  { q: 'Can I add credits without upgrading my plan?', a: 'Yes. Add-on credit packs are available on every paid tier for a month with more going on than usual.' },
  { q: 'What is the difference between a personal plan and For Teams?', a: 'Personal plans are one seat, one workspace. For Teams adds shared workspaces, pooled team credits, admin controls, and audit history across everyone on the team.' },
];

export default function Pricing() {
  const [openFaq, setOpenFaq] = useState(null);

  return (
    <PublicShell wide>
      <section className="about-hero">
        <h1>Pricing that scales with the work, not the seat.</h1>
        <p>Every plan includes the same approval-first guardrails. What changes is how much work your team can run.</p>
      </section>

      <section className="pricing-grid">
        {TIERS.map((t) => (
          <article key={t.name} className={t.featured ? 'pricing-card featured' : 'pricing-card'}>
            {t.featured && <span className="pricing-badge">Most popular</span>}
            <h2>{t.name}</h2>
            <p className="pricing-price"><strong>{t.price}</strong>{t.period}</p>
            <p className="pricing-credits">{t.credits}</p>
            <p className="pricing-tagline">{t.tagline}</p>
            <ul>
              {t.includes.map((i) => <li key={i}>{i}</li>)}
            </ul>
            <button className={t.featured ? 'primary' : 'ghost'}>Get started</button>
          </article>
        ))}
      </section>

      <section className="pricing-teams">
        <h2>For Teams</h2>
        <p>
          Startup and SMB plans add shared workspaces, team credits, approval
          controls, audit history, and administration.
        </p>
        <Link to="/for-teams" className="primary" style={{ display: 'inline-block', textDecoration: 'none' }}>
          Talk to us
        </Link>
      </section>

      <section className="about-contact">
        <h2>Credit FAQ</h2>
        <div className="faq-list">
          {FAQ.map((item, i) => (
            <div key={item.q} className="faq-item">
              <button
                type="button"
                className="faq-question"
                onClick={() => setOpenFaq(openFaq === i ? null : i)}
                aria-expanded={openFaq === i}
              >
                {item.q}
                <span aria-hidden="true">{openFaq === i ? '−' : '+'}</span>
              </button>
              {openFaq === i && <p className="faq-answer">{item.a}</p>}
            </div>
          ))}
        </div>
      </section>
    </PublicShell>
  );
}
