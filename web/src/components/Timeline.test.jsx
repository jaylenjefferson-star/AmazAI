import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Timeline, { Body } from './Timeline';

describe('Timeline message body', () => {
  it('renders a Bot’s double-star emphasis as bold text', () => {
    render(<Body text={'I am **Engle**, a **Sr Engineer**.'} />);

    expect(screen.getByText('Engle').tagName).toBe('STRONG');
    expect(screen.getByText('Sr Engineer').tagName).toBe('STRONG');
    expect(screen.queryByText(/\*\*/)).toBeNull();
  });
});

const msg = (key, text, role = 'assistant') => ({ type: 'message', key, role, author: 'Engle', text, at: '2026-01-01T10:00:00Z' });
const props = { streaming: null, typing: null, approvals: [], agents: [], onDecide: () => {}, showAuthor: false };
const rows = (c) => [...c.querySelectorAll('.tl-row')];

let scrollTo;
beforeEach(() => {
  scrollTo = vi.fn();
  HTMLElement.prototype.scrollTo = scrollTo;
});

/** Fake the geometry jsdom does not have, so "at the bottom" and "scrolled up" can be told apart. */
function geometry(el, { scrollTop, scrollHeight = 2000, clientHeight = 800 }) {
  Object.defineProperties(el, {
    scrollTop: { configurable: true, get: () => scrollTop },
    scrollHeight: { configurable: true, get: () => scrollHeight },
    clientHeight: { configurable: true, get: () => clientHeight },
  });
}

describe('Timeline animation', () => {
  it('animates nothing when a history loads', () => {
    const { container, rerender } = render(<Timeline {...props} items={[]} />);
    rerender(<Timeline {...props} items={[msg('a', 'one'), msg('b', 'two'), msg('c', 'three')]} />);
    expect(rows(container)).toHaveLength(3);
    expect(container.querySelectorAll('.is-new')).toHaveLength(0);
  });

  it('animates only the row that arrived, and keeps the DOM nodes of the rest', () => {
    const first = [msg('a', 'one'), msg('b', 'two')];
    const { container, rerender } = render(<Timeline {...props} items={first} />);
    const before = rows(container);
    rerender(<Timeline {...props} items={[...first, msg('c', 'three')]} />);
    const after = rows(container);
    expect(after.slice(0, 2)).toEqual(before);                        // same nodes: nothing was re-created
    expect(after.filter((r) => r.classList.contains('is-new')).map((r) => r.textContent)).toEqual(['three']);
  });

  it('does not re-create the rows after an insertion in the middle (no index keys)', () => {
    const a = msg('a', 'one'); const c = msg('c', 'three');
    const { container, rerender } = render(<Timeline {...props} items={[a, c]} />);
    const [rowA, rowC] = rows(container);
    rerender(<Timeline {...props} items={[a, { type: 'event', key: 'e1', text: 'Woke Engle' }, c]} />);
    const after = rows(container);
    expect(after[0]).toBe(rowA);
    expect(after[2]).toBe(rowC);                                      // an index key would have made this a new node
  });
});

describe('Timeline scrolling', () => {
  it('follows new messages instantly, and only inside its own list', () => {
    const { rerender } = render(<Timeline {...props} items={[msg('a', 'one')]} />);
    scrollTo.mockClear();
    rerender(<Timeline {...props} items={[msg('a', 'one'), msg('b', 'two')]} />);
    expect(scrollTo).toHaveBeenCalledWith({ top: expect.any(Number), behavior: 'instant' });
  });

  it('does not yank a reader who scrolled up when someone else replies', () => {
    const { container, rerender } = render(<Timeline {...props} items={[msg('a', 'one')]} />);
    const list = container.querySelector('.timeline');
    geometry(list, { scrollTop: 100 });                                // far from the bottom
    act(() => { list.dispatchEvent(new Event('scroll', { bubbles: true })); });
    scrollTo.mockClear();
    rerender(<Timeline {...props} items={[msg('a', 'one'), msg('b', 'a reply')]} />);
    expect(scrollTo).not.toHaveBeenCalled();
  });

  it('always brings you to the message you just sent', () => {
    const { container, rerender } = render(<Timeline {...props} items={[msg('a', 'one')]} />);
    const list = container.querySelector('.timeline');
    geometry(list, { scrollTop: 100 });
    act(() => { list.dispatchEvent(new Event('scroll', { bubbles: true })); });
    scrollTo.mockClear();
    rerender(<Timeline {...props} items={[msg('a', 'one'), msg('local:1:0', 'my message', 'user')]} />);
    expect(scrollTo).toHaveBeenCalledWith({ top: expect.any(Number), behavior: 'instant' });
  });
});


describe('Timeline live reply', () => {
  it('draws the words as they arrive, with a cursor', () => {
    // The renderer was already here and nothing ever fed it: both live screens
    // passed streaming={null}, so the console showed a dot over a poll.
    const { container } = render(
      <Timeline {...props} items={[]} streaming={{ text: 'Looking into it', author: 'Engle' }} />);
    expect(screen.getByText('Looking into it')).toBeTruthy();
    expect(container.querySelector('.cursor')).toBeTruthy();
  });

  it('grows the same bubble rather than adding a second one', () => {
    const { container, rerender } = render(
      <Timeline {...props} items={[]} streaming={{ text: 'Look' }} />);
    rerender(<Timeline {...props} items={[]} streaming={{ text: 'Looking into it' }} />);
    expect(container.querySelectorAll('.cursor')).toHaveLength(1);
    expect(screen.getByText('Looking into it')).toBeTruthy();
  });

  it('replaces the typing dots once the first word lands', () => {
    const { container } = render(
      <Timeline {...props} items={[]}
                streaming={{ text: 'Working on it' }} typing={{ name: 'Engle', verb: 'thinking' }} />);
    expect(screen.getByText('Working on it')).toBeTruthy();
    expect(container.querySelector('.typing-dots')).toBeNull();
  });

  it('shows the dots while a turn has produced no words yet', () => {
    const { container } = render(
      <Timeline {...props} items={[]} streaming={null} typing={{ name: 'Engle', verb: 'thinking' }} />);
    expect(container.querySelector('.typing-dots')).toBeTruthy();
    expect(container.querySelector('.cursor')).toBeNull();
  });

  it('keeps the empty state away while a reply is arriving', () => {
    render(<Timeline {...props} items={[]} streaming={{ text: 'Hi' }} />);
    expect(screen.queryByText('Say hello')).toBeNull();
  });
});



const JANEISHA_RESPONSE = [
  '### 1. What are AmazAI’s current top-level priorities and OKRs?',
  '> I own company-wide priorities and **executive alignment**.',
  '',
  '### 2. Who are the key executives / team leads, and what does each own?',
  '> I need a clear map of ownership.',
  '',
  '---',
  '',
  '1. Review the current priorities.',
  '2. Confirm each reporting line.',
].join('\n');

describe('safe Markdown in chat', () => {
  it('renders the Janeisha response as structure without raw markers', () => {
    const { container } = render(<Body text={JANEISHA_RESPONSE} />);
    const headings = [...container.querySelectorAll('h3')];
    expect(headings).toHaveLength(2);
    expect(headings[0].textContent).toContain('current top-level priorities');
    expect(screen.getByText('executive alignment').tagName).toBe('STRONG');
    expect(container.querySelectorAll('blockquote')).toHaveLength(2);
    expect(container.querySelector('hr')).toBeTruthy();
    expect([...container.querySelectorAll('ol > li')].map((n) => n.textContent))
      .toEqual(['Review the current priorities.', 'Confirm each reporting line.']);
    expect(container.textContent).not.toContain('###');
    expect(container.textContent).not.toContain('**');
    expect(container.textContent).not.toContain('---');
  });

  it('renders a GFM table as an actual table, not mushed pipes and dashes', () => {
    const table = [
      '| Priority | What | Why |',
      '|---|---|---|',
      '| 1 | Observability | Cannot manage what is not measured |',
      '| 2 | Delegation tiers | Batch operations need fewer gates |',
    ].join('\n');
    const { container } = render(<Body text={table} />);

    expect(container.querySelector('table')).toBeTruthy();
    expect([...container.querySelectorAll('th')].map((n) => n.textContent))
      .toEqual(['Priority', 'What', 'Why']);
    const rows = [...container.querySelectorAll('tbody tr')]
      .map((r) => [...r.querySelectorAll('td')].map((c) => c.textContent));
    expect(rows).toEqual([
      ['1', 'Observability', 'Cannot manage what is not measured'],
      ['2', 'Delegation tiers', 'Batch operations need fewer gates'],
    ]);
    // The raw syntax -- and specifically the header separator row, the exact
    // shape that used to render as a literal line of dashes -- is gone.
    expect(container.textContent).not.toContain('|');
    expect(container.textContent).not.toContain('---');
  });

  it('renders paragraphs, hard breaks, emphasis and code semantically', () => {
    const { container } = render(<Body text={'First soft\nline.\n\nSecond hard  \nbreak with *careful* **bold** and `code`.'} />);
    expect(container.querySelectorAll('p')).toHaveLength(2);
    expect(container.querySelectorAll('br')).toHaveLength(1);
    expect(screen.getByText('careful').tagName).toBe('EM');
    expect(screen.getByText('bold').tagName).toBe('STRONG');
    expect(screen.getByText('code').tagName).toBe('CODE');
  });

  it('handles nested CommonMark emphasis that the old regex exposed', () => {
    const { container } = render(<Body text={'***both*** and **bold *plus italic* text**'} />);
    const both = screen.getByText('both');
    expect(both.closest('em')).toBeTruthy();
    expect(both.closest('strong')).toBeTruthy();
    expect(container.textContent).not.toContain('***');
  });

  it('keeps malformed and escaped markers literal', () => {
    const { container } = render(<Body text={'**unfinished\n\n\\*\\*literal\\*\\*\n\n** not bold**'} />);
    expect(container.querySelector('strong')).toBeNull();
    expect(container.textContent).toContain('**unfinished');
    expect(container.textContent).toContain('**literal**');
  });

  it('keeps a known mention interactive inside emphasis', () => {
    const onContact = vi.fn();
    const { container } = render(
      <Body text={'Ask *@janeisha* about strategy.'} ids={['janeisha']} onContact={onContact} />);
    const mention = screen.getByRole('button', { name: 'Open janeisha contact' });
    expect(mention.closest('em')).toBeTruthy();
    fireEvent.click(mention);
    expect(onContact).toHaveBeenCalledWith('janeisha');
    expect(container.querySelectorAll('button.mention')).toHaveLength(1);
  });

  it('does not turn email, code, linked text, or unknown names into actions', () => {
    const { container } = render(
      <Body text={'person@example.com, alerts+@ops.com, `@janeisha`, [@janeisha](https://example.com), (@janeisha). Hi,@janeisha. and @nobody'}
            ids={['example', 'ops', 'janeisha']} onContact={() => {}} />);
    expect(container.querySelectorAll('button.mention')).toHaveLength(2);
    expect(container.querySelector('code')?.textContent).toBe('@janeisha');
    // The one intentional markdown link stays a plain link, not a mention
    // action -- found by its href, not by assuming it is the first <a>: GFM
    // (enabled for tables) also autolinks the bare emails below into their
    // own <a mailto:...>, which is a real, separate, still-safe link
    // (mailto: is already in safeUrl's allowlist), never a mention action.
    const intentionalLink = container.querySelector('a[href="https://example.com"]');
    expect(intentionalLink?.textContent).toBe('@janeisha');
    expect(intentionalLink?.querySelector('button')).toBeNull();
    expect(container.querySelectorAll('a button')).toHaveLength(0);
    expect(container.textContent).toContain('person@example.com');
    expect(container.textContent).toContain('alerts+@ops.com');
    expect(container.textContent).toContain('@nobody');
  });

  it('allows only intentional links and strips active content', () => {
    globalThis.__amazaiPwned = 0;
    const { container } = render(<Body text={[
      '[safe](https://example.com)',
      '[mail](mailto:test@example.com)',
      '[bad](javascript:globalThis.__amazaiPwned=1)',
      '[network](//evil.example/path)',
      '[triple](///evil.example/path)',
      '<script>globalThis.__amazaiPwned=1</script>',
      '<img src=x onerror="globalThis.__amazaiPwned=1">',
      '![image](https://example.com/x.png)',
    ].join(' ')} />);
    const hrefs = [...container.querySelectorAll('a')].map((a) => a.getAttribute('href'));
    expect(hrefs).toEqual(['https://example.com', 'mailto:test@example.com']);
    expect(container.querySelector('a')?.getAttribute('rel')).toBe('noopener noreferrer');
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('img')).toBeNull();
    expect(container.textContent).toContain('network');
    expect(container.textContent).toContain('triple');
    expect([...container.querySelectorAll('a')].some((a) => a.getAttribute('href')?.startsWith('//'))).toBe(false);
    expect(globalThis.__amazaiPwned).toBe(0);
    delete globalThis.__amazaiPwned;
  });

  it('clamps model headings below the page-level outline', () => {
    const { container } = render(<Body text={'# Model title\n\n## Section\n\n### Detail\n\n#### Small'} />);
    expect(container.querySelector('h1')).toBeNull();
    expect(container.querySelector('h2')).toBeNull();
    expect(container.querySelectorAll('h3')).toHaveLength(3);
    expect(container.querySelectorAll('h4')).toHaveLength(1);
  });

  it('does not create invalid block nesting', () => {
    const { container } = render(<Body text={JANEISHA_RESPONSE} />);
    for (const selector of ['p p', 'p ol', 'p blockquote', 'a button']) {
      expect(container.querySelector(selector)).toBeNull();
    }
  });
});

describe('Markdown while streaming', () => {
  it('uses the same renderer and hides its visual cursor from assistive tech', () => {
    const { container } = render(
      <Timeline {...props} items={[]} streaming={{ text: '**Ready**', author: 'Janeisha Carter' }} />);
    expect(screen.getByText('Ready').tagName).toBe('STRONG');
    expect(container.textContent).not.toContain('**');
    expect(container.querySelectorAll('.cursor')).toHaveLength(1);
    expect(container.querySelector('.cursor')?.getAttribute('aria-hidden')).toBe('true');
    expect(screen.getByRole('log').getAttribute('aria-busy')).toBe('true');
  });
});



describe('streaming Markdown stability', () => {
  it('keeps the existing paragraph DOM node while the buffer grows', () => {
    const mentionIds = ['janeisha']; // the memoized value Task and Room pass in production
    const { container, rerender } = render(
      <Timeline {...props} items={[]} mentionIds={mentionIds}
                streaming={{ text: 'Working', author: 'Janeisha' }} />);
    const paragraph = container.querySelector('.body--streaming p');

    rerender(
      <Timeline {...props} items={[]} mentionIds={mentionIds}
                streaming={{ text: 'Working on it', author: 'Janeisha' }} />);

    expect(container.querySelector('.body--streaming p')).toBe(paragraph);
    expect(paragraph.textContent).toBe('Working on it');
  });
});
