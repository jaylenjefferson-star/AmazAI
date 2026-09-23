import { useEffect } from 'react';

const SITE = 'https://amazai.co';

function upsertMeta(attr, key, content) {
  let el = document.querySelector(`meta[${attr}="${key}"]`);
  if (!el) {
    el = document.createElement('meta');
    el.setAttribute(attr, key);
    document.head.appendChild(el);
  }
  el.setAttribute('content', content);
}

/**
 * Sets the tab title, meta description, and share-card tags for the current
 * route. No react-helmet: an SPA has exactly one <head>, and the only thing
 * that changes between routes is a handful of tags — three DOM writes on
 * mount don't need a dependency to manage a tag queue.
 */
export default function Seo({ title, description, path }) {
  useEffect(() => {
    const fullTitle = title ? `${title} · AmazAI` : 'AmazAI';
    document.title = fullTitle;

    upsertMeta('property', 'og:title', fullTitle);
    upsertMeta('name', 'twitter:title', fullTitle);
    upsertMeta('name', 'twitter:card', 'summary_large_image');

    if (description) {
      upsertMeta('name', 'description', description);
      upsertMeta('property', 'og:description', description);
      upsertMeta('name', 'twitter:description', description);
    }

    if (path) {
      const url = `${SITE}${path}`;
      upsertMeta('property', 'og:url', url);
      let link = document.querySelector('link[rel="canonical"]');
      if (!link) {
        link = document.createElement('link');
        link.setAttribute('rel', 'canonical');
        document.head.appendChild(link);
      }
      link.setAttribute('href', url);
    }
  }, [title, description, path]);

  return null;
}
