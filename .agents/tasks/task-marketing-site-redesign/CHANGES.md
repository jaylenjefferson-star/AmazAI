# FEAT-004 changes — mobile polish + a11y/SEO/perf review (steps 19–20)

Scope was refinement only: FEAT-002/003 sections were left as-is and touched
with the smallest coherent responsive / ARIA / SEO changes. No new sections,
no reworded spec copy, no new dependencies, plain CSS only.

## Files changed

### `web/src/components/PublicShell.jsx` — mobile navigation (step 19) + ARIA (20a)
- Added an accessible **mobile disclosure menu** (hamburger). Below 860px the
  desktop primary row and inline CTAs hide and a `.mkt-nav-toggle` button
  (`aria-expanded`, `aria-controls="mkt-mobile-menu"`, dynamic `aria-label`)
  opens a `<nav id="mkt-mobile-menu" aria-label="Mobile">` sheet. The sheet
  flattens the Product mega-menu into a plain link list (a phone can't operate
  a hover panel) plus a "More" group for Solutions/Resources/Pricing and
  repeats Sign in / Start free so both CTAs stay one tap away.
- Extracted `PRIMARY_LINKS` so the desktop bar and the mobile sheet render the
  same primary items from one source.
- New `mobileOpen` state; closes on route change and on Escape (alongside the
  existing mega-menu close). Escape now closes whichever surface is open.
- Added `aria-controls="mkt-product-menu"` + matching `id` on the Product
  mega-menu so the trigger points at the popup it owns.

### `web/src/styles.css` — mobile CSS, focus, overflow (steps 19 + 20a)
- New `.mkt-nav-toggle` / `.mkt-mobile-menu` styles; the 860px breakpoint now
  hides `.mkt-nav-primary` **and** `.mkt-nav-cta` and shows the hamburger +
  sheet (previously the primary row just disappeared with no replacement).
- Added a **`:focus-visible` outline** for all marketing links/buttons/tabs
  (nav, hero, tabs, mega-menu, mobile menu) — the whole `.mkt` surface had no
  visible keyboard focus indicator before (WCAG 2.4.7 gap). Keyboard-only, so
  a mouse press never draws a ring.
- Added `.mkt { overflow-x: clip; }` as a page-wide no-horizontal-overflow
  guard. `clip` (not `hidden`) keeps the sticky nav working.
- Narrow-screen (≤560px) refinements: stack the room frame's fixed 150px rail
  under the thread (`.pf-room-cols`), and make the hero CTAs full-width so they
  don't crowd at ~360px. Existing 880/560 breakpoints were reused; no new
  breakpoints introduced.

### `web/index.html` — baseline SEO (step 20b)
- Replaced the stale default `<meta name="description">` and `og:description`
  (“A private agent operator platform.”) with the marketing positioning line.
  The before-paint theme script, `theme-color`, and OG image were left as-is.

## Verified but unchanged
- **SEO coverage:** every marketing route already renders `<Seo>` (About,
  Contact, Enterprise, FAQ, ForTeams, HowItWorks, Integrations, Legal, Pricing,
  Product, SecurityOverview, UseCases, Landing). Homepage `<Seo>` title/desc
  already reflect the new positioning — read well, left intact.
- **Public-path triple mirror** (`theme.js` PUBLIC regex, `index.html`
  before-paint regex, `main.jsx` PUBLIC_PATHS) unchanged — no routes changed.
- **Heading order:** one `<h1>` on the homepage (hero), section `<h2>`s in
  order; `<h3>` only inside product-frame markup. Team tabs use a real
  `tablist/tab/tabpanel` with roving focus + Arrow/Home/End (from FEAT-002),
  now with a visible focus ring.
- **Motion:** all entrances/keyframes remain gated by `prefers-reduced-motion`
  (both part-1 and part-2 blocks); no new motion added.
- **Performance:** no marketing component imports live app/api modules
  (`grep` of `src/screens/home/*` + `Landing.jsx` shows only react, react-router,
  `characters/Companion`, `useReveal`, `PublicShell`, `Seo`, `auth0`). No new
  runtime dependency (`git diff web/package.json` empty). Bundle essentially
  unchanged: JS 885.59 kB (gzip 266.86) vs 884.31 baseline; CSS 160.10 kB
  (gzip 29.71) vs 158.43 — the delta is the mobile-menu + focus CSS only.

## Verification
- `npm run build` — clean, `dist/index.html` produced (2.66 kB).
- `npm test` — **29 files / 197 tests passing** (baseline held).
- Live browser spot-check was not run: the sandbox blocks starting a preview
  server. Overflow/responsive behaviour was reviewed statically against the
  fixed-width elements (sidebar 168px, room rail 150px, mega panel 860px — all
  hidden or reflowed below their breakpoints) and the new `overflow-x: clip`
  guard.
