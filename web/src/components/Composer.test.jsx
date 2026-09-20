import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import Composer from './Composer';

describe('Composer', () => {
  it('sends a normal message as a redirect while a Bot is already working', async () => {
    const user = userEvent.setup();
    const onSend = vi.fn().mockResolvedValue();
    render(<Composer name="Engineering" busy onSend={onSend} />);

    await user.type(screen.getByRole('textbox', { name: 'Redirect Engineering…' }), 'Try the API instead{Enter}');

    expect(onSend).toHaveBeenCalledWith('Try the API instead', { redirect: true });
  });

  it('routes an exact slash command without sending it as a chat turn', async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    const onCommand = vi.fn().mockResolvedValue();
    render(<Composer commands={[{ key: 'remember', hint: 'Save a fact' }]} onSend={onSend} onCommand={onCommand} />);

    await user.type(screen.getByRole('textbox'), '/remember prefer concise answers{Enter}');

    expect(onCommand).toHaveBeenCalledWith('remember', 'prefer concise answers');
    expect(onSend).not.toHaveBeenCalled();
  });

  it('offers a matching Bot mention and inserts its stable id', async () => {
    const user = userEvent.setup();
    render(<Composer mentionables={[{ id: 'eng', name: 'Engineering', archetype: 'builder', color: 'blue' }]} />);

    const box = screen.getByRole('textbox');
    await user.type(box, '@en');
    await user.click(screen.getByRole('option', { name: /Engineering/i }));

    expect(box.value).toBe('@eng ');
  });
});
