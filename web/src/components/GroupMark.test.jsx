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

  it('never overlaps: there is no negative-margin stack to draw a ring around', () => {
    const { container } = render(<GroupMark members={many(3)} />);
    expect(container.querySelector('.rs-stack, .rs-stack-item')).toBeNull();
  });

  it('carries no hidden text, so copying a conversation stays clean', () => {
    const { container } = render(<GroupMark members={many(3)} />);
    expect(container.textContent).toBe('');
  });
});
