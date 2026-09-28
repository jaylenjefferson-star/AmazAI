/**
 * Social-proof framework — a scalable case-study component with EMPTY slots.
 *
 * AmazAI has no verified customer proof yet, so this fabricates nothing: no
 * logos, no testimonials, no usage counts, no performance claims. What it
 * ships is the *shape* a real case study will drop into — quote, customer,
 * workflow before AmazAI, workflow with AmazAI, measured outcome — rendered
 * as a clearly-labelled empty state so the layout is designed and reviewable
 * now, and a real story replaces the placeholder later.
 *
 * The slot labels below are structure, not claims; the visible copy says the
 * section is a placeholder. id=customers so it can be linked to later.
 */
const SLOTS = [
  { label: 'Quote', hint: 'What the customer said in their words' },
  { label: 'Company / customer', hint: 'Who they are and what they do' },
  { label: 'Workflow before AmazAI', hint: 'How the work was done before' },
  { label: 'Workflow with AmazAI', hint: 'How companions do it now' },
  { label: 'Measured outcome', hint: 'The result they can point to' },
];

export default function SocialProof() {
  return (
    <section className="mkt-section mkt-proof" id="customers">
      <div className="mkt-container">
        <div className="mkt-proof-head">
          <p className="mkt-eyebrow">Customer stories</p>
          <h2 className="mkt-title">Real results, coming soon.</h2>
          <p className="mkt-lede">
            We&rsquo;re working with early teams now. As soon as their results
            are verified, their stories will live here — in their words, with
            the outcomes they can measure.
          </p>
        </div>

        <div className="mkt-proof-card" aria-label="Case study template (coming soon)">
          <span className="mkt-proof-tag">Coming soon</span>
          <div className="mkt-proof-slots">
            {SLOTS.map((s) => (
              <div key={s.label} className="mkt-proof-slot">
                <span className="mkt-proof-slot-label">{s.label}</span>
                <span className="mkt-proof-slot-hint">{s.hint}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
