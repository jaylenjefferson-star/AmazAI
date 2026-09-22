import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import AdminShell from './AdminShell';

/**
 * The whole point of the admin surface is that it is NOT the chat app: no
 * Composer, no message input. If the shell ever pulled one in, this fails.
 */
describe('AdminShell', () => {
  it('renders the governance chrome and a link-out, but no bot-chat composer', () => {
    const { container } = render(
      <MemoryRouter initialEntries={['/admin']}>
        <AdminShell />
      </MemoryRouter>,
    );

    // The admin frame is there.
    expect(screen.getByText('AmazAI Admin')).toBeTruthy();
    expect(screen.getByRole('link', { name: /Open in member app/ })).toBeTruthy();

    // No composer / chat input. The Composer renders a textbox to type a
    // message into; the admin shell must render no such thing.
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(container.querySelector('textarea')).toBeNull();
    expect(container.querySelector('.composer')).toBeNull();
    expect(screen.queryByPlaceholderText(/message/i)).toBeNull();
  });
});
