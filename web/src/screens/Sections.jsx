import { Link } from 'react-router-dom';
import Companion, { STATES } from '../characters/Companion';
import { ARCHETYPES } from '../characters/archetypes';
import {
  fixtureAgents, fixtureArtifacts, fixtureRooms, fixtureRoutines,
} from '../fixtures';

function Page({ title, sub, children, action }) {
  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>{title}</h1>
          <p>{sub}</p>
        </div>
        {action}
      </header>
      {children}
    </div>
  );
}

export function Agents() {
  const agents = fixtureAgents();
  return (
    <Page title="Agents" sub="Your cast. Each one has its own drive, budget and grants."
          action={<Link className="btn-link primary" to="/agents/new">New companion</Link>}>
      <div className="row-list">
        {agents.map((a) => (
          <article key={a.agentId} className="row-card">
            <Companion archetype={a.archetype} color={a.color} state={a.state}
                       size={46} name={a.name} />
            <div className="row-body">
              <strong>{a.name}</strong>
              <span>{a.role} · {ARCHETYPES[a.archetype].name}</span>
            </div>
            <span className={`state-chip cc-tone-${STATES[a.state].tone}`}>
              <i className="cc-dot" aria-hidden="true" />
              {STATES[a.state].label}
            </span>
          </article>
        ))}
      </div>
    </Page>
  );
}

export function Rooms() {
  const rooms = fixtureRooms();
  const byId = Object.fromEntries(fixtureAgents().map((a) => [a.agentId, a]));
  return (
    <Page title="Rooms" sub="Several companions on one thread. Handoffs happen here.">
      <div className="row-list">
        {rooms.map((r) => (
          <article key={r.id} className="row-card">
            <div className="participants">
              {r.members.map((m) => (
                <Companion key={m} archetype={byId[m]?.archetype} color={byId[m]?.color}
                           state={byId[m]?.state} size={30} name={byId[m]?.name} />
              ))}
            </div>
            <div className="row-body">
              <strong>{r.name}</strong>
              <span>{r.last}</span>
            </div>
          </article>
        ))}
      </div>
    </Page>
  );
}

export function Routines() {
  const routines = fixtureRoutines();
  const byId = Object.fromEntries(fixtureAgents().map((a) => [a.agentId, a]));
  return (
    <Page title="Routines" sub="Work that runs on a schedule, whether or not you are here.">
      <div className="row-list">
        {routines.map((r) => {
          const a = byId[r.agent];
          return (
            <article key={r.id} className="row-card">
              <Companion archetype={a?.archetype} color={a?.color} state="idle"
                         size={38} name={a?.name} />
              <div className="row-body">
                <strong>{r.name}</strong>
                <span>{r.cadence} · next {r.next}</span>
              </div>
            </article>
          );
        })}
      </div>
    </Page>
  );
}

export function Artifacts() {
  const artifacts = fixtureArtifacts();
  return (
    <Page title="Artifacts" sub="What runs produced. Sealed bundles are never rewritten.">
      <div className="row-list">
        {artifacts.map((f) => (
          <article key={f.id} className="row-card">
            <span className="artifact-glyph" aria-hidden="true">▤</span>
            <div className="row-body">
              <strong>{f.name}</strong>
              <span>{f.kind} · {f.agent} · {f.at}</span>
            </div>
            {f.sealed && <span className="state-chip cc-tone-ok">
              <i className="cc-dot" aria-hidden="true" />Sealed</span>}
          </article>
        ))}
      </div>
    </Page>
  );
}
