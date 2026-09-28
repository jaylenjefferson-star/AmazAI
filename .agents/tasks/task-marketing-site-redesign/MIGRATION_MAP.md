# AmazAI Marketing Site — Audit & Migration Map

Audit of the existing public marketing surface (React 18 + Vite SPA in `web/`),
grounded in the actual code, with a keep / modify / replace decision per item.
This is the foundation for the 20-step redesign; FEAT-001 implements steps 1–2
(marketing design tokens + navigation). Later features (FEAT-002..004) build the
homepage sections, footer restructure, pricing restructure, and a11y/SEO/perf.

## Baseline (verified before any edit)

- `npm run build` — clean (vite 6.4.3, `dist/index.html` produced).
- `npm test` — **28 files / 194 tests passing**.

Any breakage after this point is attributable to the redesign.

## Audit + migration table

| Area | Where it lives (real path) | Decision | Rationale |
| --- | --- | --- | --- |
| **Current routes** | `web/src/main.jsx` `<Router>` — public routes: `/welcome-to-amazai` (Landing under `PublicOnly`), `/about`, `/how-it-works`, `/product`, `/pricing`, `/integrations`, `/use-cases`, `/security`, `/for-teams`, `/enterprise`, `/faq`, `/contact`, `/legal` + legal doc routes (`/terms`, `/privacy`, `/cookie-policy`, `/acceptable-use`, `/security-disclosure`) | **keep** | Route table is correct and complete. Redesign adds no new routes; mega-menu uses on-page anchors + existing routes. |
| **Public-path allowlist (3 mirrors)** | `PUBLIC_PATHS` array in `web/src/main.jsx`; `PUBLIC` regex in `web/src/theme.js`; before-paint regex in `web/index.html` | **keep** | Must stay in sync only if routes change; FEAT-001 changes none, so all three stay as-is. Documented here so later features remember the triple-mirror. |
| **Shared layout** | `web/src/components/PublicShell.jsx` (`PublicShell` wraps `TopNav` + `<main>` + `PublicFooter`) | **modify** | Keep the shell composition; rewrite `TopNav`. `Landing.jsx` renders `TopNav`/`PublicFooter` directly (not via `PublicShell`). |
| **Header / nav** | `TopNav` export in `web/src/components/PublicShell.jsx`; CSS `.public-nav*`, `.public-nav-dropdown*` in `web/src/styles.css` (~L1544, L1618) | **replace** | Current nav is 5 flat links + a Resources dropdown + "Create your AmazAI" CTA. FEAT-001 replaces it with the minimal sticky nav: Product (mega-menu) / Solutions / Resources / Pricing + Sign in / Start free. |
| **Footer** | `PublicFooter` export in `web/src/components/PublicShell.jsx`; CSS `.public-foot*` (~L1558) | **keep (FEAT-001)** → modify later | Footer restructure belongs to FEAT-003. FEAT-001 leaves it untouched. |
| **Typography + type scale** | `web/src/styles.css` `:root` `--t-*` ramp (display/title/body/strong/label/meta), `--sans`/`--mono` | **keep + extend** | App type ramp stays. Add additive marketing `--mkt-*` clamp-based fluid heading scale (editorial hero) without touching app `--t-*`. |
| **Design tokens** | `web/src/styles.css` `:root` (light default) + TWO dark blocks: `@media (prefers-color-scheme: dark)` (~L100) and `[data-theme="dark"]` — must stay in step. Brand purples `--brand-3 #8b2fe0`, `--brand-4 #c026d3`, `--brand-sweep`; accent `--accent #2b6bff` | **keep + extend (additive)** | Do NOT change shared token meanings or dark blocks. Add a scoped `.mkt` marketing token layer (white canvas, near-black ink, neutral hairlines, selective purple accent, spacing) that reuses existing brand vars. Public site is forced light (`theme.js modeForPath`), so marketing tokens only need light values. |
| **Second stylesheet** | `web/src/premium.css` (conversation-first layer, tuned for the dark app) | **keep** | App-facing; not used to style marketing. No changes. |
| **Marketing components / screens** | `web/src/screens/Landing.jsx` (hero + steps + control), `web/src/screens/marketing/*.jsx` (Product, HowItWorks, Integrations, UseCases, ForTeams, Enterprise, FAQ, Contact, Legal, SecurityOverview), `About.jsx` | **keep (FEAT-001)** → Landing replaced later | FEAT-001 does not touch screens. Landing hero + full homepage narrative are FEAT-002/003. Other marketing pages stay reachable and unchanged. |
| **Responsive breakpoints** | `web/src/styles.css` `@media` at 1180 / 760 / 720 / 620 px (marketing/footer at 720 & 620; app density at 1180/760) | **keep + extend** | Nav CSS added by FEAT-001 introduces a mobile breakpoint consistent with the existing 720/620 usage. No existing breakpoint changes. |
| **Reusable product UI as marketing visuals** | Companion system: `web/src/characters/Companion.jsx`, `archetypes.jsx`, `characters.css` (archetypes pebble/paper/jelly/cloud/lantern/moth; states idle/thinking/working/waiting/approval/complete/blocked/offline). Product surfaces: `Room.jsx`, `Rooms.jsx`, `Sections.jsx`, `Composer.jsx`, `PresenceFeed`/`CoordinationFeed`/`Delegation`/`Handoff`, `ApprovalCard.jsx`, `ConnectionBar`, `Cards.jsx`, `AgentAvatar`/`AgentProfile` | **keep (reference)** | Primary visual language for later hero/product compositions. Live app screens depend on api/auth — recreate as lightweight static compositions in FEAT-002+, do NOT import live screens. FEAT-001 only reuses `Companion` conceptually via the nav/tokens; no import churn. |
| **Signup / sign-in** | `startLogin(loginWithRedirect, { signup?, returnTo })` in `web/src/auth0.jsx` (signup sets `screen_hint: 'signup'`) | **keep** | Reuse exactly. New nav CTAs: Sign in → `startLogin(loginWithRedirect,{returnTo:'/'})`; Start free → `startLogin(loginWithRedirect,{signup:true,returnTo:'/welcome'})`. No new auth flow. |
| **Analytics** | none found — repo-wide search for gtag/posthog/segment/mixpanel/plausible returns 0 matches | **absent (note)** | No analytics library present. Do NOT add one. Noted for FEAT-004 SEO/perf review so its absence is a deliberate finding, not an oversight. |
| **SEO metadata** | Per-route `web/src/components/Seo.jsx` (title/description/canonical/OG/Twitter, upserts into the single `<head>`); baseline meta + before-paint theme script in `web/index.html` | **keep** | Every marketing screen already renders `<Seo>`. Reuse on new/edited pages; deeper SEO/OG-image review is FEAT-004. |
| **Pricing source** | `web/src/screens/marketing/Pricing.jsx` (TIERS: Explore/Personal/Personal+/Pro/Power; credits model; Stripe checkout via `api.billing.checkout`) + `Pricing.test.jsx` | **keep (FEAT-001)** → modify later | Pricing restructure is FEAT-003 and MUST keep `Pricing.test.jsx` green. FEAT-001 does not touch it. |
| **Logo / brand mark** | `web/src/components/Logo.jsx` (`LogoMark` + wordmark, SVG, theme-inverting) | **keep** | Used in nav + footer as-is. |

## Notes carried forward

- **Triple-mirror rule:** any new public route must be added to all three of
  `main.jsx PUBLIC_PATHS`, `theme.js PUBLIC`, and the `index.html` before-paint
  regex. FEAT-001 adds none.
- **Public site is always light:** `theme.js modeForPath` forces light for
  public paths, so the marketing token layer only needs light values and must
  not disturb the two dark blocks.
- **No new heavy deps:** motion is CSS-only (transitions/keyframes,
  `prefers-reduced-motion`), consistent with the Companion system.
