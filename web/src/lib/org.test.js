import { describe, expect, it } from 'vitest';
import { buildTree, countBelow, teamOf } from './org';

const bot = (agentId, managerId = null, extra = {}) => ({ agentId, name: agentId.toUpperCase(), managerId, ...extra });

describe('buildTree', () => {
  it('puts Chief at the top and the rest under it', () => {
    const tree = buildTree([bot('eng', 'chief'), bot('chief', null, { entrypoint: true }), bot('ops', 'chief')]);
    expect(tree.map((n) => n.agent.agentId)).toEqual(['chief']);
    expect(tree[0].children.map((n) => n.agent.agentId)).toEqual(['eng', 'ops']);
  });

  it('nests a team under its manager, at any depth', () => {
    const tree = buildTree([bot('chief', null, { entrypoint: true }), bot('eng', 'chief'), bot('qa', 'eng')]);
    expect(tree[0].children[0].children[0].agent.agentId).toBe('qa');
    expect(countBelow(tree[0])).toBe(2);
  });

  it('lists a Bot that reports straight to you beside Chief, Chief first', () => {
    const tree = buildTree([bot('ops', null), bot('chief', null, { entrypoint: true })]);
    expect(tree.map((n) => n.agent.agentId)).toEqual(['chief', 'ops']);
  });

  it('never loses a Bot: a missing manager or a loop lands at the top', () => {
    const tree = buildTree([bot('a', 'ghost'), bot('b', 'c'), bot('c', 'b')]);
    const seen = [];
    const walk = (n) => { seen.push(n.agent.agentId); n.children.forEach(walk); };
    tree.forEach(walk);
    expect(seen.sort()).toEqual(['a', 'b', 'c']);
  });

  it('is empty for an empty roster', () => {
    expect(buildTree([])).toEqual([]);
  });
});

describe('teamOf', () => {
  const roster = [bot('chief', null), bot('eng', 'chief'), bot('qa', 'eng'), bot('ops', 'chief')];
  it('is everyone beneath a Bot, at any depth', () => {
    expect([...teamOf('chief', roster)].sort()).toEqual(['eng', 'ops', 'qa']);
    expect([...teamOf('eng', roster)]).toEqual(['qa']);
    expect(teamOf('qa', roster).size).toBe(0);
  });
});
