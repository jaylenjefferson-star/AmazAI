import { TopNav, PublicFooter } from '../components/PublicShell';
import Seo from '../components/Seo';
import Hero from './home/Hero';
import TrustStrip from './home/TrustStrip';
import PlatformStory from './home/PlatformStory';
import TeamUseCases from './home/TeamUseCases';
import Companions from './home/Companions';
import Rooms from './home/Rooms';
import Routines from './home/Routines';
import Artifacts from './home/Artifacts';

/**
 * The public homepage.
 *
 * The narrative deliberately explains what AmazAI *does* before naming its
 * concepts: hero and trust strip set the frame, the platform story shows
 * multi-agent coordination in plain language, the team tabs make it concrete,
 * and only then do the Companions/Rooms/Routines/Artifacts sections introduce
 * the vocabulary — each anchored by an id the nav mega-menu links to.
 *
 * Sections live as small components in ./home so this file stays a readable
 * table of contents and later features can append more sections (Connectors,
 * live demo, pricing, final CTA, footer — FEAT-003).
 */
export default function Landing() {
  return (
    <div className="mkt">
      <Seo
        title="AmazAI — Your work has a new operating system"
        description="Build a team of AI companions that research, create, use your tools, run recurring work, and collaborate with you from one workspace."
        path="/welcome-to-amazai"
      />
      <TopNav />

      <main>
        <Hero />
        <TrustStrip />
        <PlatformStory />
        <TeamUseCases />
        <Companions />
        <Rooms />
        <Routines />
        <Artifacts />
      </main>

      <PublicFooter />
    </div>
  );
}
