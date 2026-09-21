import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { keyedBy } from './keyedRoute';

// A stand-in for a conversation: it owns a draft, exactly like the composer does.
function Thread() {
  const [draft, setDraft] = useState('');
  return <input aria-label="draft" value={draft} onChange={(e) => setDraft(e.target.value)} />;
}

function App({ keyed }) {
  const Screen = keyed ? keyedBy('agentId', Thread) : Thread;
  return (
    <MemoryRouter initialEntries={['/agents/eng']}>
      <Link to="/agents/res">go to research</Link>
      <Routes><Route path="/agents/:agentId" element={<Screen />} /></Routes>
    </MemoryRouter>
  );
}

describe('switching conversations', () => {
  it('does not carry a half-written message from one agent into the next', () => {
    render(<App keyed />);
    fireEvent.change(screen.getByLabelText('draft'), { target: { value: 'ship it to prod' } });
    fireEvent.click(screen.getByText('go to research'));
    expect(screen.getByLabelText('draft').value).toBe('');
  });

  it('is the bug this prevents: without the key the draft follows you', () => {
    render(<App keyed={false} />);
    fireEvent.change(screen.getByLabelText('draft'), { target: { value: 'ship it to prod' } });
    fireEvent.click(screen.getByText('go to research'));
    expect(screen.getByLabelText('draft').value).toBe('ship it to prod');
  });
});
