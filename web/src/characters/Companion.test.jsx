import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import Companion from './Companion';

describe('Companion accessibility text', () => {
  it('describes itself to a screen reader by default, with its state', () => {
    const { container } = render(<Companion name="Engle" state="working" />);
    expect(container.textContent).toContain('Engle');
    expect(container.querySelector('svg').getAttribute('role')).toBe('img');
  });

  it('adds no hidden text when it sits beside its own name, so copying a chat stays clean', () => {
    const { container } = render(<span><Companion name="Engle" decorative /> Engle</span>);
    expect(container.textContent.trim()).toBe('Engle');
    expect(container.querySelector('svg').getAttribute('aria-hidden')).toBe('true');
    expect(container.querySelector('svg').getAttribute('role')).toBeNull();
  });
});
