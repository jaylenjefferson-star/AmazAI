import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Body } from './Timeline';

describe('Timeline message body', () => {
  it('renders a Bot’s double-star emphasis as bold text', () => {
    render(<Body text={'I am **Engle**, a **Sr Engineer**.'} />);

    expect(screen.getByText('Engle').tagName).toBe('STRONG');
    expect(screen.getByText('Sr Engineer').tagName).toBe('STRONG');
    expect(screen.queryByText(/\*\*/)).toBeNull();
  });
});
