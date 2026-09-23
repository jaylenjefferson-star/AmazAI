import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import PublicShell from '../components/PublicShell';
import Seo from '../components/Seo';

/** Pulls `key: value` pairs out of a leading `---` YAML block without a YAML
 *  parser — the frontmatter here is flat scalars, so a real parser would be
 *  a dependency to read five lines. */
function splitFrontmatter(raw) {
  const m = raw.match(/^---\n([\s\S]*?)\n---\n?/);
  if (!m) return { meta: {}, body: raw };
  const meta = {};
  for (const line of m[1].split('\n')) {
    const i = line.indexOf(':');
    if (i === -1) continue;
    meta[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return { meta, body: raw.slice(m[0].length) };
}

function formatDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });
}

/** Internal doc links (`/privacy`, `/terms`, ...) route through react-router
 *  so reading one policy into the next never triggers a full page reload. */
function DocLink({ href, children }) {
  if (href?.startsWith('/')) return <Link to={href}>{children}</Link>;
  return <a href={href} target="_blank" rel="noreferrer noopener">{children}</a>;
}

/**
 * Renders one legal document from the markdown copy in `src/content`.
 *
 * These files are the source of truth for AmazAI's policies — legal wrote
 * the words, this component only lays them out — so nothing here should
 * rephrase or summarize the body text.
 */
export default function PolicyPage({ raw, description, path }) {
  const { meta, body } = useMemo(() => splitFrontmatter(raw), [raw]);
  const updated = formatDate(meta.last_updated || meta.effective);

  return (
    <PublicShell>
      <Seo title={meta.title} description={description} path={path} />
      <article className="policy">
        {updated && <p className="policy-meta">Last updated {updated}</p>}
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{ a: DocLink }}
        >
          {body}
        </ReactMarkdown>
      </article>
    </PublicShell>
  );
}
