import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import GroupMark from './GroupMark';

const member = (id) => ({ agentId: id, archetype: 'pebble', color: '#7b93ff', state: 'idle' });
const many = (n) => Array.from({ length: n }, (_, i) => member(`a${i}`));

describe('GroupMark', () => {
  it.each([[1, 1], [2, 2], [3, 3], [4, 4]])('shows %i member(s) as %i face(s) and no count', (count, faces) => {
    const { container } = render(<GroupMark members={many(count)} />);
    expect(container.querySelectorAll('.gm-cell svg')).toHaveLength(faces);
    expect(container.querySelector('.gm-more')).toBeNull();
  });

  it('shows three faces and a +N when there are more than four', () => {
    const { container } = render(<GroupMark members={many(6)} />);
    expect(container.querySelectorAll('.gm-cell svg')).toHaveLength(3);
    expect(container.querySelector('.gm-more').textContent).toBe('+3');
  });

  it.each([2, 3, 4, 5, 7])('places %i members so no two faces touch and none leaves the tile', (count) => {
    const { container } = render(<GroupMark members={many(count)} size={100} />);
    const boxes = [...container.querySelectorAll('.gm-cell')].map((el) => {
      const w = parseFloat(el.style.width); const cx = parseFloat(el.style.left) + w / 2; const cy = parseFloat(el.style.top) + w / 2;
      return { cx, cy, r: w / 2 };
    });
    for (const b of boxes) expect(Math.hypot(b.cx - 50, b.cy - 50) + b.r).toBeLessThanOrEqual(50.001);   // inside the circle
    for (let i = 0; i < boxes.length; i += 1) {
      for (let j = i + 1; j < boxes.length; j += 1) {
        expect(Math.hypot(boxes[i].cx - boxes[j].cx, boxes[i].cy - boxes[j].cy)).toBeGreaterThanOrEqual(boxes[i].r + boxes[j].r - 0.001);
      }
    }
  });

  it('never overlaps: there is no negative-margin stack to draw a ring around', () => {
    const { container } = render(<GroupMark members={many(3)} />);
    expect(container.querySelector('.rs-stack, .rs-stack-item')).toBeNull();
  });

  it('carries no hidden text, so copying a conversation stays clean', () => {
    const { container } = render(<GroupMark members={many(3)} />);
    expect(container.textContent).toBe('');
  });
});
