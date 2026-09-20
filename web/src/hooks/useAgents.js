import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';

// API rows intentionally keep the durable control-plane vocabulary. The UI
// adds only presentation defaults, so a fresh agent never turns into an
// invented companion when an optional avatar field is absent.
// The API's lifecycle words (`agents.STATUSES`) are not the character system's
// states. Passed through, `disabled` matched nothing and fell back to idle --
// a switched-off Bot drawn, and labelled, as merely resting.
const LIFECYCLE = {
  active: 'idle',
  paused: 'offline',
  disabled: 'offline',
  archived: 'offline',
  provisioning: 'thinking',   // being set up: not done, not stuck
  failed: 'blocked',
};

export function presentAgent(agent) {
  const avatar = agent?.avatar || {};
  const rawState = agent?.state || agent?.status || 'idle';
  return {
    ...agent,
    archetype: avatar.shape || agent?.archetype || 'pebble',
    color: avatar.color || agent?.accent || '#2f6fe4',
    state: LIFECYCLE[rawState] || rawState,
  };
}

export function useAgents() {
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const result = await api.agents();
      setAgents((result.agents || []).map(presentAgent));
      setError('');
    } catch (err) {
      setAgents([]);
      setError(err.message || 'Could not reach the control plane.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { reload(); }, [reload]);
  return { agents, loading, error, reload, setAgents };
}
