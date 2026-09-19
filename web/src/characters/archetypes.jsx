/**
 * AmazAI companions.
 *
 * Six archetypes with genuinely different silhouettes, because shape is the
 * thing that survives when an avatar is 18px in a sidebar row or rendered
 * for someone who cannot distinguish the accent colours. Colour is the
 * second signal, never the only one.
 *
 * Every companion is drawn from primitives — no external assets, no canvas,
 * no video. Each one is a handful of paths inside a 100×100 viewBox, so the
 * same component serves a list row and a 200px hero without a second file.
 *
 * The parts are named (`body`, `core`, `accentA`…) so the state layer in
 * `Companion.jsx` can animate the same conceptual piece across archetypes:
 * a "breathing" idle animates `.cc-body` whether that body is a pebble or a
 * moth.
 */

/* Each archetype returns its silhouette. `c` is the accent colour; `deep` is
   a darkened form of it for interior contrast, computed once by the caller. */

function Pebble({ c, deep }) {
  return (
    <>
      <path className="cc-body"
            d="M50 16c20 0 33 13 33 32 0 18-14 30-33 30S17 66 17 48c0-19 13-32 33-32Z"
            fill={c} />
      <path className="cc-shine"
            d="M33 34c4-8 12-12 20-11-9 2-15 7-18 14-2 4-4 3-2-3Z"
            fill="#fff" opacity=".45" />
      <ellipse className="cc-core" cx="50" cy="50" rx="11" ry="10" fill={deep} />
    </>
  );
}

function Paper({ c, deep }) {
  return (
    <>
      <path className="cc-body" d="M50 12 84 40 70 84H30L16 40Z" fill={c} />
      {/* The folds are the character: one lit facet, one shadowed. */}
      <path className="cc-fold-a" d="M50 12 84 40 50 52Z" fill="#fff" opacity=".34" />
      <path className="cc-fold-b" d="M50 12 16 40l34 12Z" fill="#000" opacity=".14" />
      <path className="cc-core" d="M50 52 70 84H30Z" fill={deep} />
    </>
  );
}

function Jelly({ c, deep }) {
  return (
    <>
      <path className="cc-body" d="M50 16c19 0 31 14 31 30 0 8-3 12-9 12H28c-6 0-9-4-9-12 0-16 12-30 31-30Z"
            fill={c} />
      <g className="cc-tendrils" stroke={c} strokeWidth="6" strokeLinecap="round" fill="none">
        <path d="M33 60c0 8-3 10-3 18" />
        <path d="M50 60c0 10 3 12 3 20" />
        <path d="M67 60c0 8 3 10 3 16" />
      </g>
      <ellipse className="cc-core" cx="50" cy="40" rx="12" ry="9" fill={deep} />
      <ellipse className="cc-shine" cx="39" cy="30" rx="7" ry="4" fill="#fff" opacity=".5" />
    </>
  );
}

function CloudKin({ c, deep }) {
  return (
    <>
      <g className="cc-body" fill={c}>
        <rect x="14" y="44" width="72" height="34" rx="17" />
        <rect x="26" y="26" width="34" height="34" rx="16" />
        <rect x="54" y="34" width="28" height="28" rx="14" />
      </g>
      <rect className="cc-core" x="38" y="48" width="24" height="16" rx="8" fill={deep} />
      <rect className="cc-shine" x="32" y="32" width="14" height="9" rx="4.5"
            fill="#fff" opacity=".45" />
    </>
  );
}

function Lantern({ c, deep }) {
  return (
    <>
      <path className="cc-handle" d="M38 24c0-9 24-9 24 0" stroke={c} strokeWidth="5"
            fill="none" strokeLinecap="round" />
      <path className="cc-body" d="M32 30h36c4 0 6 3 5 7l-6 40c-1 5-4 7-8 7H41c-4 0-7-2-8-7l-6-40c-1-4 1-7 5-7Z"
            fill={c} />
      <ellipse className="cc-core" cx="50" cy="54" rx="12" ry="13" fill={deep} />
      <path className="cc-shine" d="M38 38h6l-3 26h-5Z" fill="#fff" opacity=".4" />
    </>
  );
}

function Moth({ c, deep }) {
  return (
    <>
      <g className="cc-wings" fill={c}>
        <path className="cc-wing-l" d="M47 46C36 26 14 24 12 40c-2 14 12 22 24 24 8 1 15-6 11-18Z" />
        <path className="cc-wing-r" d="M53 46c11-20 33-22 35-6 2 14-12 22-24 24-8 1-15-6-11-18Z" />
      </g>
      <ellipse className="cc-body" cx="50" cy="56" rx="8" ry="20" fill={deep} />
      <circle className="cc-core" cx="50" cy="44" r="7" fill={c} />
      <g className="cc-antennae" stroke={deep} strokeWidth="3" strokeLinecap="round" fill="none">
        <path d="M47 36c-3-6-7-8-11-9" />
        <path d="M53 36c3-6 7-8 11-9" />
      </g>
    </>
  );
}

export const ARCHETYPES = {
  pebble:  { key: 'pebble',  name: 'Pebble',  blurb: 'Calm and steady. Sits still until it has something worth saying.', Shape: Pebble },
  paper:   { key: 'paper',   name: 'Paper',   blurb: 'Folded and precise. Reorganizes itself while it thinks.',          Shape: Paper },
  jelly:   { key: 'jelly',   name: 'Jelly',   blurb: 'Light and curious. Drifts toward whatever is interesting.',        Shape: Jelly },
  cloud:   { key: 'cloud',   name: 'Cumulus', blurb: 'Warm and organized. Keeps the pieces in tidy stacks.',             Shape: CloudKin },
  lantern: { key: 'lantern', name: 'Lantern', blurb: 'Quiet and bright. Good in the parts nobody else lit.',             Shape: Lantern },
  moth:    { key: 'moth',    name: 'Moth',    blurb: 'Quick and searching. Finds the thing and comes back.',             Shape: Moth },
};

export const ARCHETYPE_KEYS = Object.keys(ARCHETYPES);

/** Suggested pairings — a starting point in the picker, never a constraint. */
export const ROLE_SUGGESTIONS = {
  engineering: 'paper',
  operations: 'pebble',
  research: 'jelly',
  chief_of_staff: 'cloud',
  finance: 'lantern',
  scout: 'moth',
};
