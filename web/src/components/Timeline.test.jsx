import { act, render, screen } from '@testing-library/react';
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
