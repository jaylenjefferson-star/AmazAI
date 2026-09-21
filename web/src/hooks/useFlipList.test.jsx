import { render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useRef } from 'react';
import { useFlipList } from './useFlipList';

function List({ rows }) {
  const ref = useRef(null);
  useFlipList(ref, rows.map((r) => r.key).join('|'));
  return <ul ref={ref}>{rows.map((r) => <li key={r.key} data-key={r.key} data-top={r.top}>{r.key}</li>)}</ul>;
}

describe('useFlipList', () => {
  const animate = vi.fn();
  beforeEach(() => {
    animate.mockClear();
    Object.defineProperty(HTMLElement.prototype, 'offsetTop', { configurable: true, get() { return Number(this.dataset.top || 0); } });
    HTMLElement.prototype.animate = animate;
    window.matchMedia = () => ({ matches: false });
  });
  afterEach(() => { delete HTMLElement.prototype.animate; });

  it('slides a row that moved from where it was, and leaves the ones that did not', () => {
    const { rerender } = render(<List rows={[{ key: 'a', top: 0 }, { key: 'b', top: 70 }]} />);
    expect(animate).not.toHaveBeenCalled();                        // first paint: nothing moved
    rerender(<List rows={[{ key: 'b', top: 0 }, { key: 'a', top: 70 }]} />);
    const shifts = animate.mock.calls.map(([frames]) => frames[0].transform).sort();
    expect(shifts).toEqual(['translateY(-70px)', 'translateY(70px)']);
  });

  it('does not animate a row that stayed put', () => {
    const { rerender } = render(<List rows={[{ key: 'a', top: 0 }, { key: 'b', top: 70 }]} />);
    rerender(<List rows={[{ key: 'a', top: 0 }, { key: 'b', top: 70 }, { key: 'c', top: 140 }]} />);
    expect(animate).not.toHaveBeenCalled();
  });

  it('plays no animation for someone who asked for reduced motion', () => {
    window.matchMedia = () => ({ matches: true });
    const { rerender } = render(<List rows={[{ key: 'a', top: 0 }, { key: 'b', top: 70 }]} />);
    rerender(<List rows={[{ key: 'b', top: 0 }, { key: 'a', top: 70 }]} />);
    expect(animate).not.toHaveBeenCalled();
  });
});
